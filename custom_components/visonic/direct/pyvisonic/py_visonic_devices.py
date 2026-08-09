"""Visonic Manage Devices - Sensors and Switches."""

# ruff: noqa: G004, C901

import logging
from typing import Any

from .py_abstract_classes import AlSwitchDevice, GenericDeviceType
from .py_const import (
    DEFAULT_DL_CODE,
    FORCE_DOWNLOAD_TO_USE_EPROM,
    KEEP_ALIVE_PERIOD,
    OBFUS,
    notknown,
)
from .py_enum import (
    CFG,
    EPROM,
    AlPanelMode,
    AlSensorCondition,
    IndexName,
    PanelSetting,
    PanelStatusNames,
)
from .py_eprom import EPROMManager
from .py_generic_device import AlGenericDeviceHelper
from .py_panel_settings import (
    PanelSettingCodesType,
    pmPanelSettingCodes,
    pmZoneChimeKey,
    pmZoneName,
    pmZoneTypeKey,
)
from .py_panel_type_data import pmPanelConfig, pmPanelType
from .py_sensor import AlSensorDeviceHelper
from .py_sensor_types import KeyfobType, pmSirenMaster, pmZoneEventAction
from .py_switch import AlSwitchDeviceHelper
from .py_utils import hexify, titlecase, toString
from .py_visonic_protocol_base import ProtocolBase

log = logging.getLogger(__name__)

INVALID_VALUES = (0, 255)

# This class manages the devices: Sensors, Switches, Sirens, Panel and Partitions
class ManageDevices(ProtocolBase):
    """Handle decoding of Visonic messages."""

    def __init__(self, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, loop = None, logger = None) -> None:
        """Perform transactions based on messages (and not bytes)."""
        super().__init__(loop=loop, force_standard_mode=force_standard_mode, disable_all_commands=disable_all_commands, download_code=download_code, user_code_slot=user_code_slot, logger=logger)
        self.PanelModel = "Unknown Panel"

    def _reset_full(self):
        """Reset all non-permanent variables."""
        super()._reset_full()
        self.PanelCapabilities : dict[IndexName, int]= {}
        self.epromManager : EPROMManager = EPROMManager()
        self.PanelStatus : dict[PanelStatusNames, Any] = {}                # This is the set of EPROM settings shown
        self.PanelType : int = 17                                # We do not yet know the paneltype so set default settings
        self.AutoEnrol : bool = True
        self.AutoSyncTime : bool = False
        # These are populated when we know the panel type from the 3C message
        self.KeepAlivePeriod : int = KEEP_ALIVE_PERIOD
        self.pmInitSupportedByPanel : bool = False
        self.ABMessageSupported : bool = True
        self.pmDownloadByEPROM : bool = False
        self.PanelSettings : dict[PanelSetting, PanelSettingCodesType] = {}              # This is the record of settings for the integration to work
        for key, value in pmPanelSettingCodes.items():
            self.PanelSettings[key] = value.default.copy()     # populate each setting with the default

    def _reset_connection(self):
        """Reset the variables needed to make a new connection."""
        super()._reset_connection()
        # Keep a dict of the sensors so we know if its new or existing
        self.SensorList: dict[int, AlSensorDeviceHelper] = {}
        # Keep a dict of the switches so we know if its new or existing
        self.SwitchList: dict[int, AlSwitchDeviceHelper] = {}
        # Keep a dict of the generic devices so we know if its new or existing
        self.DeviceList: dict[str, AlGenericDeviceHelper] = {}
        self.PanelModel = "Unknown Panel"

    def _shutdown(self):
        """Shutdown the connection to the panel."""
        super()._shutdown()
        # empty the EPROM data when stopped
        self.epromManager.reset()

    def sensor_change_handler(self, sensor : AlSensorDeviceHelper, s : AlSensorCondition):
        """Handler when there is a change to a sensor."""
        #self._dumpAllDevicesToLogFile()

    def switch_change_handler(self, switch : AlSwitchDevice):
        """Handler when there is a change to a switch."""
        #self._dumpAllDevicesToLogFile(True)

    def get_partitions_in_use(self) -> set[int] | None:
        """Get partitions in use. If a panel does not have partitions then return None."""
        # If partitions are enabled in the panel then return the partition set
        #     note that the set could only be a single partition (if that is what is set in the panel)
        if self.partitionsEnabled:
            return self.PartitionsInUse
        return None

    def checkAndAddPartition(self, max_partition_count: int, p: int) -> set:
        """The variable p is bit encoded e.g. p=5 means partitions 1 and 3."""
        part = set()
        # Are partitions enabled in the panel
        partitions_enabled_in_panel = self.PanelSettings[PanelSetting.PartitionEnabled] not in (0, 255)    # Are partitions enabled in the panel? i think that == 1 enables partitions
        for i in range(max_partition_count):
            f = (1 << i) & p
            if f != 0:
                part.add(i)
                if partitions_enabled_in_panel and i not in self.PartitionsInUse:
                    if not self.partitionsEnabled:
                        log.info("[checkAndAddPartition]     partitions enabled")
                        self.partitionsEnabled = True
                    self.PartitionsInUse.add(i)  # overall used partitions, this is a set so no repetitions allowed
        return part

    def _get_panel_setting(self, p : PanelSetting, offset : int) -> str | int | bool | None:
        """Get a panel setting."""
        # Do not use for usercodes
        if p is not None and offset is not None and p in self.PanelSettings and offset < len(self.PanelSettings[p]):
            return self.PanelSettings[p][offset]   # could be a list or a bytearray
        return None

    def _is_valid_user_code(self) -> bool:
        """Is the usercode in the panel settings valid?"""
        ucs = self.user_code_slot * 2  # 2 bytes per User Code
        return not self.ForceStandardMode and len(self.PanelSettings[PanelSetting.UserCodes]) >= ucs  # and self.PanelSettings[PanelSetting.UserCodes][ucs-2] != 0 and self.PanelSettings[PanelSetting.UserCodes][ucs-1] != 0

    def _get_user_code(self):
        """Get the usercode from the panel settings."""
        if self._is_valid_user_code():
            ucs = self.user_code_slot * 2  # 2 bytes per User Code
            #log.debug(f"[_get_user_code] {self.PanelSettings[PanelSetting.UserCodes]}")
            #log.debug(f"[_get_user_code] {self.PanelSettings[PanelSetting.UserCodes][ucs-2]}  {self.PanelSettings[PanelSetting.UserCodes][ucs-1]}")
            return bytearray([self.PanelSettings[PanelSetting.UserCodes][ucs-2], self.PanelSettings[PanelSetting.UserCodes][ucs-1]])
        return bytearray([0,0])

    def _get_panel_capability(self, i : IndexName) -> int:
        """Get a panel capability."""
        if i is not None and i in self.PanelCapabilities:
            return self.PanelCapabilities[i]    # always an integer
#        if self.Panel
        return 0

    def _check_panel_data_present(self, forceall, output_to_log) -> tuple[set[PanelSetting], set[PanelSetting]]:
        #zone_count = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
        zone_count = self._get_panel_capability(IndexName.ZONES)
        if self.is_power_master():
            need_these = {PanelSetting.ZoneNames       : zone_count,
                          PanelSetting.ZoneNameString  : 21,            # All panels have 31 zone names (21 fixed and 10 user defined) e.g. Living Roon, Kitchen etc
                          PanelSetting.ZoneCustNameStr : 10,            # 10 user defined zones
                          PanelSetting.ZoneTypes       : pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.DeviceTypesZones: pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.ZoneEnrolled    : zone_count,
                          PanelSetting.PanelBypass     : 1,             # This is a string so ensure a min length of 1 character
                          PanelSetting.ZoneChime       : zone_count,
                          PanelSetting.ZoneDelay       : zone_count,
                          PanelSetting.HasPGM          : 1,
                          #PanelSetting.PanelSerial     : 1,
                          #PanelSetting.PanelDownload   : 1,
                          PanelSetting.PartitionData   : self._get_panel_capability(IndexName.PARTITIONS),
                          #PanelSetting.PanelName       : 1,
                          PanelSetting.PartitionEnabled: 1,
                          }
        else:
            need_these = {PanelSetting.ZoneNames       : zone_count,
                          PanelSetting.ZoneTypes       : pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.DeviceTypesZones: pmPanelConfig[CFG.DEV_ZONE_TYPES][self.PanelType],
                          PanelSetting.ZoneEnrolled    : zone_count,
                          PanelSetting.PanelBypass     : 1,             # This is a string so ensure a min length of 1 character
                          PanelSetting.ZoneChime       : zone_count
                          }

        if not self.ForceStandardMode:
            need_these[PanelSetting.UserCodes] = 2 * self._get_panel_capability(IndexName.USERS)

        if output_to_log and forceall:
            log.debug("[_check_panel_data_present]  forceall is True")
        optional = set()
        mandatory = set()
        for s,v in need_these.items():
            m = pmPanelSettingCodes[s].mandatory
            if output_to_log and not OBFUS:
                if s in self.PanelSettings:
                    if forceall or v > len(self.PanelSettings[s]):
                        log.debug(f"[_check_panel_data_present]     {s.name:<15}   want {v}  got {len(self.PanelSettings[s])}    {'mandatory' if m else 'optional'}")
                else:
                    log.debug(f"[_check_panel_data_present]     {s.name:<15}   want {v}  s not in panelsettings    {'mandatory' if m else 'optional'}")
            if forceall or not (s in self.PanelSettings and len(self.PanelSettings[s]) >= v):
                if m:
                    mandatory.add(s)
                else:
                    optional.add(s)
        return (mandatory, optional)

    def _update_sensor(self, sensor_identifier: int) -> bool:
        """Common function to update sensor parameters from a message from the panel."""

        def getPanelStringName(zone_name_ref: int) -> str | None:
            cust_name = self.PanelSettings[PanelSetting.ZoneCustNameStr]
            zone_name = self.PanelSettings[PanelSetting.ZoneNameString]
            if len(cust_name) == 31 and 0 <= zone_name_ref <= 30:
                return self._get_panel_setting(PanelSetting.ZoneCustNameStr, zone_name_ref)
            if len(zone_name) == 21 and len(cust_name) == 10 and 0 <= zone_name_ref <= 30:
                return self._get_panel_setting(PanelSetting.ZoneNameString, zone_name_ref) if zone_name_ref <= 20 else self._get_panel_setting(PanelSetting.ZoneCustNameStr, zone_name_ref - 21)
            log.debug(f"[_update_sensor]    getPanelStringName  NOT OK, got missing data       {zone_name_ref=}    {len(zone_name)=}    {len(cust_name)=}")
            log.debug(f"[_update_sensor]               {zone_name}")
            log.debug(f"[_update_sensor]               {cust_name}")
            return None

        (mandatory, _optional) = self._check_panel_data_present(forceall = False, output_to_log = False)
        #m = mandatory | optional
        if not self.ForceStandardMode and len(mandatory) > 0:
            log.debug(f"[_update_sensor]       Not Forcing Standard and not got all mandatory panel settings so not updating sensor {mandatory=}")
            return False

        enrolled        = self._get_panel_setting(PanelSetting.ZoneEnrolled,     sensor_identifier)
        zone_type       = self._get_panel_setting(PanelSetting.ZoneTypes,        sensor_identifier)
        zone_chime      = self._get_panel_setting(PanelSetting.ZoneChime,        sensor_identifier)
        device_type     = self._get_panel_setting(PanelSetting.DeviceTypesZones, sensor_identifier)
        motiondelaytime = self._get_panel_setting(PanelSetting.ZoneDelay,        sensor_identifier)
        zn              = self._get_panel_setting(PanelSetting.ZoneNames,        sensor_identifier)
        partition_data  = self._get_panel_setting(PanelSetting.PartitionData,    sensor_identifier)

        zone_name_ref   = zn & 0x1F if isinstance(zn, int) else 0
        zone_panel_name = None if zn is None else getPanelStringName(zone_name_ref)

        #log.debug(f"[_update_sensor]     partitiondata set as {self.PanelSettings[PanelSetting.PartitionData] if PanelSetting.PartitionData in self.PanelSettings else "Undefined"}")

        if enrolled is None or not enrolled:
            if sensor_identifier in self.SensorList:
                log.info(f"[_update_sensor]       Removing sensor Z{(sensor_identifier+1):0>2} as it is not enrolled in Panel EPROM Data or B0 Enrolled Data")
                if self.onNewSensorHandler is not None:
                    self.onNewSensorHandler(create=False, py_sensor=self.SensorList[sensor_identifier])
                del self.SensorList[sensor_identifier]
                return True
            return False

        part = {0}
        partition_count = self._get_panel_capability(IndexName.PARTITIONS)
        if isinstance(partition_data, int) and partition_count > 1:
            part = self.checkAndAddPartition(partition_count, partition_data)  # this returns the partitions that this sensor belongs to

        updated = False
        created_new_sensor = False

        if sensor_identifier not in self.SensorList:
            self.SensorList[sensor_identifier] = AlSensorDeviceHelper( id = sensor_identifier + 1 )
            created_new_sensor = True

        zone_name = "not_installed"
        if sensor_identifier < len(self.PanelSettings[PanelSetting.ZoneNames]):
            zone_name = pmZoneName[zone_name_ref]

        # By here the sensor exists in the dictionary
        sensor = self.SensorList[sensor_identifier]

        if zn is not None and sensor.zone_name != zone_name:
            updated = True
            sensor.zone_name = zone_name

        if zone_panel_name is not None and sensor.zone_panel_name != zone_panel_name:
            updated = True
            sensor.zone_panel_name = zone_panel_name

        if isinstance(device_type, int):
            if sensor.raw_sensor_id != device_type:
                updated = True
                sensor.raw_sensor_id = device_type

        if isinstance(zone_chime, int) and 0 <= zone_chime <= 2:
            #log.debug(f"[_update_sensor]   Setting Zone Chime {zone_chime}  {pmZoneChimeKey[zone_chime]}")
            self.PanelSettings[PanelSetting.ZoneChime][sensor_identifier] = zone_chime
            if sensor.zone_chime != pmZoneChimeKey[zone_chime]:
                updated = True
                # set but never used sensor.zchimeref = zone_chime
                if zone_chime < len(pmZoneChimeKey):
                    sensor.zone_chime = pmZoneChimeKey[zone_chime]
                else:
                    sensor.zone_chime = "undefined " + str(zone_chime)
                    log.debug(f"[_update_sensor] {notknown} Found unknown chime type {zone_chime}")

        zone_types = self.PanelSettings[PanelSetting.ZoneTypes]
        if zone_type is not None:
            zone_types[sensor_identifier] = zone_type
        elif sensor_identifier < len(zone_types):
            zone_type = zone_types[sensor_identifier]
        else:
            zone_type = None

        if isinstance(zone_type, int) and sensor.zone_type != zone_type:
            updated = True
            sensor.zone_type = zone_type
            if zone_type < len(pmZoneTypeKey):
                sensor.zone_type_name = pmZoneTypeKey[zone_type]
            else:
                sensor.zone_type_name = "undefined " + str(zone_type)   # undefined
                log.debug(f"[_update_sensor] {notknown} Found unknown zonetype type {zone_type}")

        #if motiondelaytime is not None and motiondelaytime != 0xFFFF:
        #    if sensor.motion_delay_time != motiondelaytime:
        #        updated = True
        #        sensor.motion_delay_time = motiondelaytime

        if self.get_partitions_in_use() is not None and sensor.partition != part:
            updated = True
            log.debug(f"[_update_sensor]     Change to partition list - sensor {sensor.id}   {part=}")
            # If we get EPROM data, assume it is all correct and override any existing settings (as some were assumptions)
            for p in part:
                sensor.add_to_partition(p)

        # if the new value is True and the old Value is False then push change enrolled
        enrolled_push_change = (enrolled and not sensor.enrolled) if sensor.enrolled is not None and enrolled is not None else False
        if enrolled is not None:
            sensor.enrolled = enrolled

        if created_new_sensor:
            sensor.add_callback(self.sensor_change_handler)
            if self.onNewSensorHandler is not None:
                self.onNewSensorHandler(create=True, py_sensor=sensor)

        # Enrolled is only sent on enrol and not on change to not enrolled
        if enrolled_push_change:
            sensor.notify(AlSensorCondition.ENROLLED)
            log.debug(f"[_update_sensor]  Zone Z{(sensor_identifier+1):>02} : {enrolled=} {zone_type=} {zone_chime=} {device_type=} {motiondelaytime=} {zn=} {partition_data=}")
        elif updated:
            sensor.notify(AlSensorCondition.STATE)
            log.debug(f"[_update_sensor]  Zone Z{(sensor_identifier+1):>02} : {enrolled=} {zone_type=} {zone_chime=} {device_type=} {motiondelaytime=} {zn=} {partition_data=}")
        #else:
        #    sensor.notify(AlSensorCondition.RESET)

        # Has something changed?
        return enrolled_push_change or updated

    def _process_EPROM_keypads_sirens(self) -> str:

        def logSetting(msg: str, setting: list):
            log.debug(f"[_process_EPROM_keypads_sirens] EPROM Decode for {msg}")

            for i, row in enumerate(setting):
                hex_values = " ".join(hexify(v) for v in row)
                log.debug(f"[_process_EPROM_keypads_sirens]        {msg} {i}   in hex: {hex_values}")

        #siren_count = pmPanelConfig[CFG.SIRENS][self.PanelType]
        siren_count = self._get_panel_capability(IndexName.SIRENS)
        keypad1w_count = self._get_panel_capability(IndexName.KEYPADS_ONE_WAY)
        keypad2w_count = self._get_panel_capability(IndexName.KEYPADS_TWO_WAY)

        device_str = ""
        setting = self.epromManager.lookupEprom(EPROM.KEYFOB_MAX)
        log.debug(f"[_process_EPROM_keypads_sirens] keyfob {setting}")
        if self.onNewDeviceHandler is not None:
            for i,f in enumerate(setting):
                device_id = AlGenericDeviceHelper.make_key(GenericDeviceType.KEYFOB, i)
                if f != 0 and device_id not in self.DeviceList:
                    key_fob_type = (f >> 16) & 0x0f
                    unknown1 = f & 0x0f
                    unknown2 = (f >> 8) & 0x0f
                    unknown3 = (f >> 24) & 0x0f
                    log.info(f"[Process Settings]   id={i+1}     keyfob type {key_fob_type}     unknown1={unknown1}     unknown2={unknown2}     unknown3={unknown3}")
                    if key_fob_type > 0 and unknown3 > 0:
                        # This is a bit of a guess, especially unknown3 being > 0 but that's what the data on my panel showed
                        device_str = f"{device_str},KP{i+1:0>2}"
                        model = KeyfobType.get(key_fob_type) if key_fob_type in KeyfobType else "KeyFob " + str(key_fob_type)
                        self.DeviceList[device_id] = AlGenericDeviceHelper(t=GenericDeviceType.KEYFOB, id = i+1, model = model, device_name = "KeyFob", enabled = True)
                        self.onNewDeviceHandler(True, self.DeviceList[device_id])

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Devices (Sirens and Keypads)
        # ------------------------------------------------------------------------------------------------------------------------------------------------
        if self.is_power_master(): # PowerMaster models
            # Process keypad settings
            setting = self.epromManager.lookupEprom(EPROM.KEYPAD_MAS)
            logSetting("keypad2", setting)
            for i in range(min(len(setting), keypad2w_count)):
                device_id = AlGenericDeviceHelper.make_key(GenericDeviceType.KEYPAD2, i)
                if device_id not in self.DeviceList and any(v not in INVALID_VALUES for v in setting[i][:5]):
                    log.debug(f"[_process_EPROM_keypads_sirens] Found an Enrolled PowerMaster keypad-2way {i}")
                    device_str = f"{device_str},KT{i+1:0>2}"
                    model = "Unknown Model"
                    self.DeviceList[device_id] = AlGenericDeviceHelper(t=GenericDeviceType.KEYPAD2, id = i+1, model=model, device_name = "KeyPad-2W", enabled = True)
                    self.onNewDeviceHandler(True, self.DeviceList[device_id])
            # Process siren settings
            setting = self.epromManager.lookupEprom(EPROM.SIRENS_MAS)
            self.PanelSettings[PanelSetting.SirenEnrolled] = [
                any(v not in INVALID_VALUES for v in setting[i])
                for i in range(min(len(setting), siren_count))
            ]
            logSetting("siren", setting)
            for i in range(min(len(setting), siren_count)):
                #self.PanelSettings[PanelSetting.SirenEnrolled].append(v)
                # if any(v not in INVALID_VALUES for v in setting[i]):
                # if any(v not in INVALID_VALUES for v in setting[i][:5]):
                if any(v not in INVALID_VALUES for v in setting[i][:5]):
                    log.debug(f"[_process_EPROM_keypads_sirens] Found an Enrolled PowerMaster siren {i}")
                    device_str = f"{device_str},S{i+1:0>2}"
        else:
            # Process keypad settings
            setting = self.epromManager.lookupEprom(EPROM.KEYPAD_1_MAX)
            logSetting("keypad1", setting)
            for i in range(min(len(setting), keypad1w_count)):
                device_id = AlGenericDeviceHelper.make_key(GenericDeviceType.KEYPAD1, i)
                if device_id not in self.DeviceList and any(v not in INVALID_VALUES for v in setting[i][:2]):
                    log.debug(f"[_process_EPROM_keypads_sirens] Found an Enrolled PowerMax 1-way keypad {i}")
                    device_str = f"{device_str},KW{i+1:0>2}"
                    model = "Unknown Model"
                    self.DeviceList[device_id] = AlGenericDeviceHelper(t=GenericDeviceType.KEYPAD1, id = i+1, model=model, device_name = "KeyPad-1W", enabled = True)
                    self.onNewDeviceHandler(True, self.DeviceList[device_id])

            setting = self.epromManager.lookupEprom(EPROM.KEYPAD_2_MAX)
            logSetting("keypad2", setting)
            for i in range(min(len(setting), keypad2w_count)):
                device_id = AlGenericDeviceHelper.make_key(GenericDeviceType.KEYPAD2, i)
                if device_id not in self.DeviceList and any(v not in INVALID_VALUES for v in setting[i][:3]):
                    log.debug(f"[_process_EPROM_keypads_sirens] Found an Enrolled PowerMax 2-way keypad {i}")
                    device_str = f"{device_str},KT{i+1:0>2}"
                    model = "Unknown Model"
                    self.DeviceList[device_id] = AlGenericDeviceHelper(t=GenericDeviceType.KEYPAD2, id = i+1, model=model, device_name = "KeyPad-2W", enabled = True)
                    self.onNewDeviceHandler(True, self.DeviceList[device_id])

            # Process siren settings
            setting = self.epromManager.lookupEprom(EPROM.SIRENS_MAX)
            logSetting("siren", setting)
            self.PanelSettings[PanelSetting.SirenEnrolled] = [
                any(v not in INVALID_VALUES for v in setting[i][:3])
                for i in range(min(len(setting), siren_count))
            ]
            for i in range(min(len(setting), siren_count)):
                if any(v not in INVALID_VALUES for v in setting[i][:3]):
                    log.debug(f"[_process_EPROM_keypads_sirens] Found a PowerMax siren {i}")
                    device_str = f"{device_str},S{i+1:0>2}"

        return device_str[1:]

    def _set_data_from_panel_type(self, p: int, pmForceDownloadByEPROM: bool) -> bool:
        if p in pmPanelType:
            self.PanelType = p

            if self.DownloadCodeUserSet:
                log.debug(f"[_set_data_from_panel_type] Using the defined Download Code {self.DownloadCode if not OBFUS else "OBFUSCATED"}")
            elif self.DownloadCode == DEFAULT_DL_CODE:
                # If the panel still has its startup default Download Code, or if it hasn't been set by the user to something different
                self.DownloadCode = pmPanelConfig[CFG.DLCODE_1][self.PanelType]
                self.PanelSettings[PanelSetting.PanelDownload] = self.DownloadCode
                log.debug(f"[_set_data_from_panel_type] Setting Download Code from the Default value {DEFAULT_DL_CODE} to the default Panel Value {self.DownloadCode}")
            else:
                log.debug(f"[_set_data_from_panel_type] Using Download Code {self.DownloadCode}")

            if 0 <= self.PanelType <= len(pmPanelConfig[CFG.SUPPORTED]) - 1:
                is_supported = pmPanelConfig[CFG.SUPPORTED][self.PanelType]
                if is_supported:
                    self.PanelModel = pmPanelType.get(self.PanelType, "Unknown Panel") # INTERFACE : PanelType set to model
                    self.PowerMaster = pmPanelConfig[CFG.POWERMASTER][self.PanelType]
                    self.AutoEnrol = pmPanelConfig[CFG.AUTO_ENROL][self.PanelType]
                    self.AutoSyncTime = pmPanelConfig[CFG.AUTO_SYNCTIME][self.PanelType]
                    self.KeepAlivePeriod = pmPanelConfig[CFG.KEEPALIVE][self.PanelType]
                    self.pmInitSupportedByPanel = pmPanelConfig[CFG.INIT_SUPPORT][self.PanelType]
                    self.ABMessageSupported = pmPanelConfig[CFG.AB_SUPPORTED][self.PanelType]
                    self.pmDownloadByEPROM = FORCE_DOWNLOAD_TO_USE_EPROM or pmForceDownloadByEPROM or pmPanelConfig[CFG.EPROM_DOWNLOAD][self.PanelType]

                    self.PanelCapabilities[IndexName.REPEATERS] = pmPanelConfig[CFG.REPEATERS][self.PanelType]
                    self.PanelCapabilities[IndexName.PANIC_BUTTONS] = 1
                    self.PanelCapabilities[IndexName.SIRENS] = pmPanelConfig[CFG.SIRENS][self.PanelType]
                    self.PanelCapabilities[IndexName.ZONES] = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
                    self.PanelCapabilities[IndexName.KEYPADS_ONE_WAY] = pmPanelConfig[CFG.ONE_WKEYPADS][self.PanelType]
                    self.PanelCapabilities[IndexName.KEYPADS_TWO_WAY] = pmPanelConfig[CFG.TWO_WKEYPADS][self.PanelType]
                    self.PanelCapabilities[IndexName.KEYFOBS] = pmPanelConfig[CFG.KEYFOBS][self.PanelType]
                    self.PanelCapabilities[IndexName.USERS] = pmPanelConfig[CFG.USERCODES][self.PanelType]
                    self.PanelCapabilities[IndexName.SWITCHES] = pmPanelConfig[CFG.SWITCH][self.PanelType]
                    self.PanelCapabilities[IndexName.GSM_MODULES] = 1
                    self.PanelCapabilities[IndexName.POWERLINK] = 1
                    self.PanelCapabilities[IndexName.PROXTAGS] = pmPanelConfig[CFG.PROXTAGS][self.PanelType]
                    self.PanelCapabilities[IndexName.PGM] = pmPanelConfig[CFG.PGM][self.PanelType]
                    self.PanelCapabilities[IndexName.PANEL] = 1
                    self.PanelCapabilities[IndexName.GUARDS] = 1
                    self.PanelCapabilities[IndexName.PARTITIONS] = pmPanelConfig[CFG.PARTITIONS][self.PanelType]
                    self.PanelCapabilities[IndexName.UNK15] = 1
                    self.PanelCapabilities[IndexName.UNK16] = 0
                    self.PanelCapabilities[IndexName.EXPANDER_33] = 0
                    self.PanelCapabilities[IndexName.IOV] = 0
                    self.PanelCapabilities[IndexName.UNK19] = 0
                    self.PanelCapabilities[IndexName.UNK20] = 0

                    return True
                # Panel 0 i.e original PowerMax
                log.error("Lookup of Visonic Panel type reveals that this seems to be a PowerMax Panel and supports EPROM Download only with no capability, this Panel cannot be used with this Integration")
                return False
        # Then it is an unknown panel type
        log.error(f"Lookup of Visonic Panel type {p} reveals that this is a new Panel Type that is unknown to this Software. Please contact the Author of this software")
        return False

    def _process_EPROM_settings(self) -> None:
        """Process Settings from the downloaded EPROM data from the panel."""

        # _process_EPROM_settings
        #    Decode the EPROM and the various settings to determine
        #       The general state of the panel
        #       The zones and the sensors
        #       The switch devices
        #       The phone numbers
        #       The user pin codes

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Panel type and serial number
        #     This checks whether the EPROM settings have been downloaded OK

        #pmDisplayName = self.epromManager.lookupEpromSingle(EPROM.DISPLAY_NAME)

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Need the panel type to be valid so we can decode some of the remaining downloaded data correctly
        # when we get here then self.PanelType is set and it's a known panel type i.e. if self.PanelType is not None and self.PanelType in pmPanelType is TRUE
        # ------------------------------------------------------------------------------------------------------------------------------------------------

        # self._dumpEPROMSettings()

        #log.debug(f"[Process Settings] Panel Type Number {str(self.PanelType)}   serial string {toString(panelSerialType)}")
        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Panel Status to display in the user interface
        self.PanelStatus.update(self.epromManager.processEPROMData(self.is_power_master()))

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process Panel Settings to use as a common panel settings regardless of how they were obtained.  This way gets them from EPROM.
        if self.is_power_master(): # PowerMaster models
            for key, value in pmPanelSettingCodes.items():
                if value.PMasterEPROM is not None and value.PMasterEPROM in EPROM:
                    if value.item is not None:
                        self.PanelSettings[key] = self.epromManager.lookupEprom(value.PMasterEPROM)[value.item]
                    else:
                        self.PanelSettings[key] = self.epromManager.lookupEprom(value.PMasterEPROM)
        else:
            for key, value in pmPanelSettingCodes.items():
                if value.PMaxEPROM is not None and value.PMaxEPROM in EPROM:
                    if value.item is not None:
                        self.PanelSettings[key] = self.epromManager.lookupEprom(value.PMaxEPROM)[value.item] # [pmPanelSettingCodes[key].item]
                    else:
                        self.PanelSettings[key] = self.epromManager.lookupEprom(value.PMaxEPROM)

        log.info("[Process Settings]     UpdatePanelSettings")

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process panel type and serial
        #pmPanelTypeCodeStr = self.PanelSettings[PanelSetting.PanelModel]      # self.epromManager.lookupEpromSingle(EPROM.PANEL_MODEL_CODE)
        #idx = f"{hex(self.PanelType).upper()[2:]:0>2}{hex(int(pmPanelTypeCodeStr)).upper()[2:]:0>2}"
        #pmPanelName = pmPanelName_t[idx] if idx in pmPanelName_t else "Unknown_" + idx

        #log.debug(f"[Process Settings]   Processing settings - panel code index {idx}")

        #  INTERFACE : Add this param to the status panel first
        #self.PanelStatus[PanelStatusNames.PANEL_NAME] = pmPanelName

        #log.warning(f"[Process Settings]    Installer Code {toString(self.epromManager.lookupEpromSingle(EPROM.INSTALLERCODE))}")
        #log.warning(f"[Process Settings]    Master DL Code {toString(self.epromManager.lookupEpromSingle(EPROM.MASTERDLCODE))}")
        #if self.is_power_master():
        #    log.debug(f"[Process Settings]    Master Code {toString(self.epromManager.lookupEpromSingle(EPROM.MASTERCODE))}")
        #    log.debug(f"[Process Settings]    Installer DL Code {toString(self.epromManager.lookupEpromSingle(EPROM.INSTALDLCODE))}")

        #log.warning(f"[Process Settings]    AlarmLED10 {self.epromManager.lookupEprom("AlarmLED10")}")
        #log.warning(f"[Process Settings]    AlarmLED30 {self.epromManager.lookupEprom("AlarmLED30")}")
        bell = self.epromManager.lookupEpromSingle("bellTime")
        log.info(f"[Process Settings] Bell Time {type(bell)=}   {bell=}")
        # Set all partitions regardless of which are actually used for panel status
        if isinstance(bell, int):
            self.PartitionState[0].setBellTime(bell * 60)
            self.PartitionState[1].setBellTime(bell * 60)
            self.PartitionState[2].setBellTime(bell * 60)

        # ------------------------------------------------------------------------------------------------------------------------------------------------
        # Process zone settings

        #zonesignalstrength = self.PanelSettings[PanelSetting.ZoneSignal]

        # For zone_data these 2 get the same data block but they are structured differently
        # PowerMax
        #    It is 30 zones, each is 4 bytes
        #        2 = Sensor Type
        #        3 = Zone Type
        #      e.g. cd ce e4 0c
        # PowerMaster
        #    It is 64 zones, each is 1 byte, represents Zone Type
        zone_data = self.PanelSettings[PanelSetting.ZoneData]

        # This is 640 bytes, PowerMaster only.
        # It is 64 zones, each is 10 bytes
        #    5 = Sensor Type
        pmaster_zone_ext_data = self.PanelSettings[PanelSetting.ZoneExt] # self.epromManager.lookupEprom(EPROM.ZONEEXT_MAS)

        for index , value in enumerate(pmaster_zone_ext_data):
            log.debug(f"[Process Settings]   Raw pmaster_zone_ext_data {index:<3} = {toString(value)}")

        #zone_count = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
        zone_count = self._get_panel_capability(IndexName.ZONES)

        log.debug(f"[Process Settings]     Zones Data Buffer      zone_count {zone_count}    len settings {len(zone_data)}     len ZoneNames {len(self.PanelSettings[PanelSetting.ZoneNames])}")
        log.debug(f"[Process Settings]         Zones Names Buffer :  {toString(self.PanelSettings[PanelSetting.ZoneNames])}")
        #log.debug(f"[Process Settings]     Zones Data Buffer  :  {zone_data}")

        if len(zone_data) > 0:
            self.PanelSettings[PanelSetting.ZoneTypes] = bytearray(zone_count)
            self.PanelSettings[PanelSetting.DeviceTypesZones] = bytearray(zone_count)
            self.PanelSettings[PanelSetting.ZoneChime] = bytearray(zone_count)
            self.PanelSettings[PanelSetting.ZoneEnrolled] = [False] * zone_count  # bytearray(zone_count)
            motiondel = [0 for i in range(zone_count)]

            for i in range(zone_count):
                if self.is_power_master():  # PowerMaster models
                    self.PanelSettings[PanelSetting.ZoneEnrolled][i] = pmaster_zone_ext_data[i][4:9] != bytearray.fromhex("00 00 00 00 00") and pmaster_zone_ext_data[i][4:6] != bytearray.fromhex("FF FF")
                    self.PanelSettings[PanelSetting.ZoneTypes][i] = int(zone_data[i])
                    self.PanelSettings[PanelSetting.ZoneChime][i] = int(zone_data[i])
                    self.PanelSettings[PanelSetting.DeviceTypesZones][i] = int(pmaster_zone_ext_data[i][5])  # 5 = Sensor Type
                    motiondel[i] = self.PanelSettings[PanelSetting.ZoneDelay][i][0] + (256 * self.PanelSettings[PanelSetting.ZoneDelay][i][1])
                else:
                    self.PanelSettings[PanelSetting.ZoneEnrolled][i] = zone_data[i][0:3] != bytearray.fromhex("00 00 00")
                    self.PanelSettings[PanelSetting.ZoneTypes][i] = (int(zone_data[i][3])) & 0x0F
                    self.PanelSettings[PanelSetting.ZoneChime][i] = ((int(zone_data[i][3])) >> 4 ) & 0x03
                    self.PanelSettings[PanelSetting.DeviceTypesZones][i] = int(zone_data[i][2])  # 2 = Sensor Type
                    motiondel[i] = 0
            self.PanelSettings[PanelSetting.ZoneDelay] = motiondel

            # ------------------------------------------------------------------------------------------------------------------------------------------------
            # Store partition info & check if partitions are on

            max_partition_count = self._get_panel_capability(IndexName.PARTITIONS)
            if max_partition_count > 1:  # Could the panel have more than 1 partition?
                partition = self.PanelSettings[PanelSetting.PartitionData]
                if partition is not None and len(partition) >= zone_count:
                    if (partitions_enabled_in_panel := self.PanelSettings[PanelSetting.PartitionEnabled] not in (0, 255)):  # i think that == 1 enables partitions
                        # If all values in the array are the same then there are assumed to be no partitions
                        self.partitionsEnabled = not all(x == partition[0] for x in partition[:zone_count])
                        if self.partitionsEnabled:
                            for x in partition[:zone_count]:
                                self.checkAndAddPartition(max_partition_count, x)
                        else:
                            log.info("[Process Settings]     The partition data all represent the same partition value, so partitions disabled")
                        if len(self.PartitionsInUse) < 2:
                            self.partitionsEnabled = False
                    else:
                        log.info("[Process Settings]     self.PanelSettings[PanelSetting.PartitionEnabled] indicates partitions disabled")
                # If that panel type can have more than 1 partition, then check to see if the panel has defined more than 1
                log.info(f"[Process Settings]     max_partition_count = {max_partition_count}    partitions_enabled_in_panel = {partitions_enabled_in_panel}    Partition Data {toString(partition[:zone_count]) if partition is not None else "Invalid"}")
            else:
                log.info(f"[Process Settings]     max_partition_count = {max_partition_count}    coded settings define a single partition")

    def _update_all_sirens(self) -> bool:
        count = self._get_panel_capability(IndexName.SIRENS)
        se = self.PanelSettings.get(PanelSetting.SirenEnrolled, [])
        dt = self.PanelSettings.get(PanelSetting.DeviceTypesSirens, bytearray())
        log.debug(f"[Process Settings]     Updating sirens {se=}  {toString(dt)=}")
        for i in range(min(count, len(se), len(dt))):
            if se[i]:
                log.debug(f"[Process Settings]       Siren {i} enrolled, device type {dt[i]}    {pmSirenMaster[dt[i]].name if dt[i] in pmSirenMaster else "Unknown Device"}")
            #else:
            #    log.debug(f"[Process Settings]       Siren {i} not enrolled")
        return False

    def _update_all_sensors(self) -> bool:

        if self.PanelType is None:
            return False

        (mandatory, _optional) = self._check_panel_data_present(forceall = False, output_to_log = False)

        retval = False
        # Do not create or update sensors until all mandatory data has been obtained
        if self.ForceStandardMode or len(mandatory) == 0:
            # Only when we have all EPROM or B0 Zone Data, or we're in Standard Emulation Mode

            log.debug("[Process Settings]   Processing Zone devices")

            #zone_count = pmPanelConfig[CFG.WIRELESS][self.PanelType] + pmPanelConfig[CFG.WIRED][self.PanelType]
            zone_count = self._get_panel_capability(IndexName.ZONES)
            for i in range(zone_count):

                tmp = self._update_sensor( sensor_identifier = i )
                retval = retval or tmp

            if (piu := self.get_partitions_in_use()) is not None:
                log.debug(f"[Process Settings]                I see that you have {piu} partition(s) set in the panel")
            else:
                log.debug("[Process Settings]                I see that you have no partitions")

        else:
            log.debug(f"[_update_all_sensors]   _check_panel_data_present missing mandatory items {mandatory=}")

        return retval # return True if any of the sensor data has been changed because of this function

    def _process_zone_event(self, event_device, event_type):
        log.debug(f"[_process_zone_event]      Zone Event      Zone: {event_device}    Type: {event_type}")
        key = event_device - 1  # get the key from the zone - 1

        if self.PanelMode in [AlPanelMode.STANDARD, AlPanelMode.MINIMAL_ONLY, AlPanelMode.STANDARD_PLUS, AlPanelMode.POWERLINK_BRIDGED, AlPanelMode.POWERLINK] and key not in self.SensorList and event_type > 0:
            log.debug("[_process_zone_event]          Got a Zone Sensor that I did not know about so creating it")
            self._update_sensor(sensor_identifier = key)

        if key in self.SensorList and event_type in pmZoneEventAction:
            sf = getattr(self.SensorList[key], pmZoneEventAction[event_type].func if event_type in pmZoneEventAction else "")
            if sf is not None:
                log.debug(f"[_process_zone_event]               Processing event {event_type}  calling {pmZoneEventAction[event_type].func}({pmZoneEventAction[event_type].parameter})")
                sf(pmZoneEventAction[event_type].parameter)
            self.SensorList[key].problem = pmZoneEventAction[event_type].problem
        else:
            log.debug(f"[_process_zone_event]               Not processing device/zone {event_device}   event {event_type}")

    def sensors_to_string_list(self) -> list[str]:
        """Dump the sensor list to a string list."""
        retval = []
        for key, sensor in self.SensorList.items():
            retval.append(f"key {key:<2} Sensor {sensor}")
        return retval

    def switches_to_string_list(self) -> list[str]:
        """Dump the switch list to a string list."""
        retval = []
        for key, switch in self.SwitchList.items():
            retval.append(f"key {key:<2} Switch {switch}")
        return retval

    def _dumpAllDevicesToLogFile(self, inc_switches = True, inc_devices = True):
        """Dump the sensor and switch list to the log file for debugging."""
        log.debug("================================================================================ Display Sensors ================================================================================")
        for key, sensor in self.SensorList.items():
            log.debug(f"     key {key:<2} Sensor {sensor}")
        if inc_switches and len(self.SwitchList) > 0:
            log.debug("  =========== Display Switches ==========")
            for key, device in self.SwitchList.items():
                log.debug(f"     key {key:<2} Switch    {device}")
        if inc_devices and len(self.DeviceList) > 0:
            log.debug("  =========== Display Devices  ==========")
            for key, device in self.DeviceList.items():
                log.debug(f"     key {key:<2} Device    {device}")

        log.debug("  =========== Panel State  ==========")
        pm = titlecase(self.PanelMode.name.replace("_"," ")) # str(AlPanelMode()[self.PanelMode]).replace("_"," ")
        log.debug(f"   Model {self.PanelModel: <18}     PowerMaster {'Yes' if self.PowerMaster else 'No': <10}     Mode   {pm: <18}     ")
        part = self.get_partitions_in_use()
        if part is not None:
            for piu in part:
                if piu is None:
                    p = self.PartitionState[0]
                    r = 'Yes' if p.PanelReady else 'No'
                    i = 'Yes' if p.PanelIntruderStatus else 'No'
                    ts = titlecase(p.determineTrouble())                   # str(AlTroubleType()[self.PanelTroubleStatus]).replace("_"," ")
                    al = titlecase(p.PanelAlarmStatus.name)                     # str(AlAlarmType()[self.PanelAlarmStatus]).replace("_"," ")
                    pn = titlecase(p.PanelStateData.name)
                    log.debug(f"                                      Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}      IntruderStatus {i: <12}")
                elif 0 <= piu <= 2:
                    p = self.PartitionState[piu]
                    r = 'Yes' if p.PanelReady else 'No'
                    i = 'Yes' if p.PanelIntruderStatus else 'No'
                    ts = titlecase(p.determineTrouble())                   # str(AlTroubleType()[self.PanelTroubleStatus]).replace("_"," ")
                    al = titlecase(p.PanelAlarmStatus.name)                     # str(AlAlarmType()[self.PanelAlarmStatus]).replace("_"," ")
                    pn = titlecase(p.PanelStateData.name)
                    log.debug(f"   Partition {piu:<1}    Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}      IntruderStatus {i: <12}")
                else:
                    log.debug(f"   Partition {piu:<1}    Invalid")

        else:
            p = self.PartitionState[0]
            r = 'Yes' if p.PanelReady else 'No'
            i = 'Yes' if p.PanelIntruderStatus else 'No'
            ts = titlecase(p.determineTrouble())                   # str(AlTroubleType()[self.PanelTroubleStatus]).replace("_"," ")
            al = titlecase(p.PanelAlarmStatus.name)                     # str(AlAlarmType()[self.PanelAlarmStatus]).replace("_"," ")
            pn = titlecase(p.PanelStateData.name)
            log.debug(f"                                      Ready {r: <13}  Status {pn: <18}      Trouble {ts: <13}      AlarmStatus {al: <12}      IntruderStatus {i: <12}")
        log.debug("================================================================================================================================================================================")
