"""Helper classes for the coordinator."""

import asyncio
from collections.abc import Callable
import re
from typing import Any, NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import UNDEFINED, UndefinedType
from homeassistant.util import slugify

from .const import (
    ALARM_COMMAND_EVENT,
    ALARM_PANEL_CHANGE_EVENT,
    ALARM_PANEL_LOG_FILE_COMPLETE,
    ALARM_PANEL_LOG_FILE_ENTRY,
    ALARM_SENSOR_CHANGE_EVENT,
    CONF_EMULATION_MODE,
    CONF_ENABLE_SENSOR_BYPASS,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_SWITCH,
    DOMAIN,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
    PARTITION_ID_WHEN_BASE,
    PE_PARTITION,
    PLATFORMS,
)
from .image_manager import ImageManager
from .log_events import logEvents
from .panel_event_logger import PanelEventLogger
from .utils import (
    create_device_label,
    create_device_unique_id,
    create_sensor_label,
    create_sensor_unique_id,
    create_siren_unique_id,
    create_switch_label,
    create_switch_unique_id,
    getAlarmPanelUniqueIdent,
    parse_int_list,
    to_bool,
)
from .visonic_entity_types import (
    AlarmPanelData,
    AlarmSensorType,
    BinaryImageDownloadData,
    BinarySensorData,
    DeviceState,
    FloatSensorData,
    SensorOnTimeout,
    SensorState,
    SwitchState,
    VisonicBinarySensorKey,
    VisonicFloatSensorKey,
    ZoneSensorData,
)
from .visonic_types import (
    AlarmCommandStatus,
    AvailableNotifications,
    EmulationMode,
    PanelCondition,
    VisonicConfigEntry,
)

MESSAGE_REASON_DICT = {
    AlarmCommandStatus.SUCCESS: "Success, sent Command to Panel",
    AlarmCommandStatus.FAIL_DOWNLOAD_IN_PROGRESS: "Failed to Send Command To Panel, not supported when downloading EPROM",
    AlarmCommandStatus.FAIL_INVALID_CODE: "Failed to Send Command To Panel, not allowed without valid pin",
    AlarmCommandStatus.FAIL_USER_CONFIG_PREVENTED: "Failed to Send Command To Panel, disabled by user settings",
    AlarmCommandStatus.FAIL_INVALID_STATE: "Failed to Send Command To Panel, invalid state requested",
    AlarmCommandStatus.FAIL_SWITCH_PROBLEM: "Failed to Send Command To Panel, general Switch Problem",
    AlarmCommandStatus.FAIL_PANEL_CONFIG_PREVENTED: "Failed to Send Command To Panel, disabled by panel settings",
    AlarmCommandStatus.FAIL_ENTITY_INCORRECT: "Failed to Send Command To Panel, entity not supported",
    AlarmCommandStatus.FAIL_PANEL_NO_CONNECTION: "Failed to Send Command To Panel, no connection to panel",
    AlarmCommandStatus.FAIL_ABSTRACT_CLASS_NOT_IMPLEMENTED: "Failed to Send Command To Panel, report error to integration author and send a log file",
}

class HA_Event_Type(NamedTuple):
    """Represents a Home Assistant event type with a name and action."""
    name: str
    action: str

# fmt: off
AlarmPanelEventActionList: dict[PanelCondition, HA_Event_Type]= {
    PanelCondition.ZONE_UPDATE                : HA_Event_Type(ALARM_SENSOR_CHANGE_EVENT,     ""),
    PanelCondition.PANEL_UPDATE               : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "panelupdate"),
    PanelCondition.PANEL_RESET                : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "panelreset"),
    PanelCondition.IMAGE_UPDATE               : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "imageupdate"),
    PanelCondition.PIN_REJECTED               : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "pinrejected"),
    PanelCondition.DOWNLOAD_TIMEOUT           : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutdownload"),
    PanelCondition.WATCHDOG_TIMEOUT_GIVINGUP  : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutwaiting"),
    PanelCondition.WATCHDOG_TIMEOUT_RETRYING  : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutactive"),
    PanelCondition.NO_DATA_FROM_PANEL         : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "nopaneldata"),
    PanelCondition.DOWNLOAD_SUCCESS           : HA_Event_Type(None, None),
    PanelCondition.STARTUP_SUCCESS            : HA_Event_Type(None, None),
    PanelCondition.PUSH_CHANGE                : HA_Event_Type(None, None),
    PanelCondition.CONNECTION                 : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "connection"),
    PanelCondition.PANEL_LOG_COMPLETE         : HA_Event_Type(ALARM_PANEL_LOG_FILE_COMPLETE, ""),
    PanelCondition.PANEL_LOG_ENTRY            : HA_Event_Type(ALARM_PANEL_LOG_FILE_ENTRY,    ""),
    PanelCondition.CHECK_ARM_DISARM_COMMAND   : HA_Event_Type(ALARM_COMMAND_EVENT,           "armdisarm"),
    PanelCondition.CHECK_BYPASS_COMMAND       : HA_Event_Type(ALARM_COMMAND_EVENT,           "bypass"),
    PanelCondition.CHECK_EVENT_LOG_COMMAND    : HA_Event_Type(ALARM_COMMAND_EVENT,           "eventlog"),
    PanelCondition.CHECK_SWITCH_COMMAND       : HA_Event_Type(ALARM_COMMAND_EVENT,           "switch")
}
# fmt: on

class PlatformManager:
    """Generic Platform Manager."""

    def __init__(
        self,
        hass: HomeAssistant,
        panelident: int,
        entry: ConfigEntry,
        logger: logEvents,
        image_manager: ImageManager,
        state_changed_callback: Callable[..., None] | None = None,
    ) -> None:
        """Initialize the Event Logger."""
        self.hass = hass
        self.entry = entry
        self.logger = logger
        self.panel_ident = panelident
        self.image_manager = image_manager
        self.state_changed_callback: Callable[..., None] | None = state_changed_callback

        v = EmulationMode(entry.data.get(CONF_EMULATION_MODE, EmulationMode.POWERLINK))
        self.force_standard_mode = v == EmulationMode.STANDARD
        self.disable_all_panel_commands = v == EmulationMode.MINIMAL
        # If disable all commands then force standard is set to True
        if self.disable_all_panel_commands:
            self.force_standard_mode = True

        self.rationalised_ha_devices = False

        self.visonic_alarm_setup_lock = asyncio.Lock()
        self.exclude_switch_list: list[int] = []
        self.exclude_sensor_list: list[int] = []

        # Use a Generic to manage the lists of sensors and switches.
        #    The functions in this class do not need to know the internals,
        #       we just add/remove them from the dictionaries
        # Dictionary of sensors that are current and valid
        self._sensor_dict: dict[int, SensorState] = {}
        # Dictionary of switches that are current and valid
        self._switch_dict: dict[int, SwitchState] = {}
        # Dictionary of devices that are current and valid, e.g. fobs, sirens etc.
        self._device_dict: dict[int, DeviceState] = {}

        # Dictionary of sensors that have been created (and do not need creating again)
        self._sensor_created_set : set[int] = set()
        # Dictionary of switches that have been created (and do not need creating again)
        self._switch_created_set : set[int] = set()
        # Dictionary of generic devices that have been created (and do not need creating again)
        self._device_created_set : set[int] = set()
        # A set of sensor IDs that are PIR/Camera and have an image
        self._image_created_set: set[int] = set()

        self._createdAlarmPanel = False
        self.created_download_active_sensor = False

        self.panel_event_log: PanelEventLogger = PanelEventLogger(
            hass=self.hass,
            panelident=self.panel_ident,
            entry=entry,
            logger=self.logger,
            create_ha_fire_event=self.create_ha_fire_event,
        )
        self.panel_entity_name: dict[int, str] = {}
        self.update()

    def update(self):
        """Updated user config."""

        # Dictionary of sensors that are current and valid
        self._sensor_dict.clear()
        # Dictionary of switches that are current and valid
        self._switch_dict.clear()
        # Dictionary of devices that are current and valid, e.g. fobs, sirens etc.
        self._device_dict.clear()

        # Process the exclude switch list
        tmp: list[int] | str = self.entry.data.get(CONF_EXCLUDE_SWITCH, "")
        if isinstance(tmp, str):
            self.exclude_switch_list = parse_int_list(tmp)
        else:
            self.exclude_switch_list = tmp

        # Process the exclude sensor list
        self.exclude_sensor_list: list[int] = []
        tmp: list[int] | str = self.entry.data.get(CONF_EXCLUDE_SENSOR, "")
        if isinstance(tmp, str):
            self.exclude_sensor_list = parse_int_list(tmp)
        else:
            self.exclude_sensor_list = tmp

        self.rationalised_ha_devices = False

        self.logger.logstate_debug("Exclude sensor list = %s", self.exclude_sensor_list)
        self.logger.logstate_debug("Exclude switch list = %s", self.exclude_switch_list)

    def create_ha_fire_event(
        self, event_id: PanelCondition, datadictionary: dict[str, Any]
    ) -> None:
        """Fire an HA Event in to HA with the associated data dictionary."""
        # Check to ensure variables are set correctly
        if self.hass is not None:
            # Event ID must be in the list to fire an HA event out
            if event_id in AlarmPanelEventActionList:
                name = AlarmPanelEventActionList[event_id].name
                if name is not None:
                    event_action = AlarmPanelEventActionList[event_id].action
                    # Base event dictionary
                    dd: dict[str, Any] = {
                        PANEL_ATTRIBUTE_NAME: self.panel_ident,
                        **(datadictionary.copy() if datadictionary else {}),
                    }
                    # Include action if present
                    if event_action:
                        dd["action"] = event_action
                    # Default panel_id
                    panel_id = f"{Platform.ALARM_CONTROL_PANEL}.{slugify(getAlarmPanelUniqueIdent(self.panel_ident))}"
                    pe_part: set[int] | int | None = dd.get(PE_PARTITION)
                    candidates: list[int] | set[int] = (
                        [pe_part]
                        if isinstance(pe_part, int)
                        else pe_part if isinstance(pe_part, set) else []
                    )
                    # Add partition is set
                    partition_index = next(
                        (p for p in candidates if p in self.panel_entity_name), None
                    )
                    if partition_index is not None:
                        self.logger.logstate_debug(
                            "Client [fire] pe_part=%s  panel_entity_name=%s",
                            pe_part,
                            self.panel_entity_name,
                        )
                        panel_id = f"{Platform.ALARM_CONTROL_PANEL}.{slugify(self.panel_entity_name[partition_index])}"
                        dd[PE_PARTITION] = partition_index + 1
                    elif isinstance(pe_part, set):
                        dd.pop(PE_PARTITION, None)
                    # Add panel_id
                    dd["panel_id"] = panel_id
                    self.logger.logstate_info(
                        "Client (panel %s) [fire] Sending HA Event %s  with data %s",
                        self.panel_ident,
                        name,
                        dd,
                    )
                    # Fire the HA Event :)
                    self.hass.bus.fire(name, dd)
            else:
                # Capture invalid event_ids just in case
                self.logger.logstate_warning(
                    "Attempt to generate HA event with unknown event_id %s",
                    event_id,
                )
        else:
            self.logger.logstate_warning(
                "Attempt to generate HA event when hass is undefined"
            )

    def generate_event_output(
        self,
        event_id: PanelCondition,
        reason: AlarmCommandStatus,
        command: str,
        message: str,
        partition: set[int] | None = None,
    ) -> dict:
        """Generate an HA Bus Event with a Reason Code."""
        full_message = message + " " + MESSAGE_REASON_DICT[reason]
        datadict = self.populateSensorDictionary()
        datadict["command"] = command.title()
        datadict["reason"] = int(reason)
        datadict["reason_str"] = reason.name.title()
        datadict["message"] = full_message
        if partition is not None:
            datadict[PE_PARTITION] = partition
        self.create_ha_fire_event(event_id, datadict)
        if reason != AlarmCommandStatus.SUCCESS:
            self.logger.create_ha_notification(
                AvailableNotifications.COMMAND,
                full_message,
            )
        return datadict

    def set_partition_name(
        self, partition: int | None = None, panel_entity_name: str | None = None
    ):
        """Set the partition naming for the alarm panel entities."""
        if (
            panel_entity_name is not None
            and partition is not None
            and 0 <= partition <= 2
        ):
            self.panel_entity_name[partition] = panel_entity_name

    def setup_visonic_entity(
        self, specific_domain: str, data: Any
    ):  # param is the parameter passed to the creating function
        """Setup a platform and add an entity using the dispatcher."""
        entry_id = self.entry.entry_id
        async_dispatcher_send(
            self.hass, f"{DOMAIN}_{entry_id}_add_{specific_domain}", data
        )

    def set_alarm_device_information(self, model: str | None = UNDEFINED):
        """Set the alarm panel device information in Home Assistant."""
        device_registry = dr.async_get(self.hass)
        device_registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, getAlarmPanelUniqueIdent(self.panel_ident))},
            name=getAlarmPanelUniqueIdent(self.panel_ident),
            manufacturer=MANUFACTURER,
            model=model,
        )

    def create_alarm_panel(self, piu: set[int] | None = None) -> AlarmPanelData:
        """On startup we create a sensor to report progress and status."""
        panel_unique_id = getAlarmPanelUniqueIdent(self.panel_ident)
        siren_id = 1  # Siren number 1
        siren_name = create_siren_unique_id(self.panel_ident, siren_id)
        apd = AlarmPanelData(panel_unique_id, piu, siren_id, siren_name)

        if self.disable_all_panel_commands:
            self.logger.logstate_debug("Creating Sensor for Alarm indications: %s", str(apd))
            self.setup_visonic_entity(Platform.SENSOR, apd)
        else:
            self.logger.logstate_debug("Creating Alarm Panel Entities: %s", str(apd))
            self.setup_visonic_entity(Platform.ALARM_CONTROL_PANEL, apd)
        return apd

    async def async_setup_alarm_panel(self, piu: set[int] | None):
        """Setup the alarm panel (async)."""
        # This sets up the Alarm Panel, or the Sensor to represent a panel state
        #   It is called from multiple places, the first one wins
        async with self.visonic_alarm_setup_lock:
            if not self._createdAlarmPanel:
                self._createdAlarmPanel = True
                #model = self.visonicProtocol.getPanelModel()
                apd: AlarmPanelData = self.create_alarm_panel(piu)
                if not self.disable_all_panel_commands:
                    self.setup_visonic_entity(Platform.SIREN, apd)
                    puid = apd.identifier
                    self.setup_visonic_entity(
                        Platform.BINARY_SENSOR,
                        [
                            # device_id not used for panel sensors
                            BinarySensorData(identifier=puid, device_id=-1, sensor_definition=VisonicBinarySensorKey.PANEL_BATTERY, initial_state=None, timeout_type=SensorOnTimeout.NO_TIMEOUT),
                            BinarySensorData(identifier=puid, device_id=-1, sensor_definition=VisonicBinarySensorKey.PANEL_PROBLEM, initial_state=None, timeout_type=SensorOnTimeout.NO_TIMEOUT),
                            BinarySensorData(identifier=puid, device_id=-1, sensor_definition=VisonicBinarySensorKey.PANEL_TAMPER, initial_state=None, timeout_type=SensorOnTimeout.NO_TIMEOUT)
                        ],
                    )
            if self.state_changed_callback:
                self.state_changed_callback()

    def rationalise_ha_devices(self, force: bool):
        """Rationalise Home Assistant devices and entities to remove any that are no longer valid."""

        if not force and self.rationalised_ha_devices:
            return

        entry_id = self.entry.entry_id
        self.rationalised_ha_devices = True

        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)

        # All entities created by this config entry
        entities = er.async_entries_for_config_entry(entity_reg, entry_id)

        entity_map: dict[str, tuple[dict[int, SensorState | SwitchState | DeviceState], set[int]]] = {
            "z": (self._sensor_dict, self._sensor_created_set),
            "x": (self._switch_dict, self._switch_created_set),
            "d": (self._device_dict, self._device_created_set),
            "kf": (self._device_dict, self._device_created_set),
            "kp": (self._device_dict, self._device_created_set),
            "kt": (self._device_dict, self._device_created_set),
            "du": (self._device_dict, self._device_created_set),
        }

        etype_pattern = "|".join(
            map(re.escape, sorted(entity_map.keys(), key=len, reverse=True))
        )

        panel = rf"p{self.panel_ident}_" if self.panel_ident > 0 else ""
        _reg_pattern = re.compile(
            rf"{DOMAIN}_{panel}"
            rf"(?P<etype>{etype_pattern})"
            r"(?P<id>\d{1,2})"
            r"[_a-z]*"
        )

        for entity in entities:
            uid = entity.unique_id
            if uid and (m := _reg_pattern.fullmatch(uid)):
                entity_type = m["etype"]
                visonic_id = int(m["id"])
                if entity_type in entity_map:
                    valid_dict, created_list = entity_map[entity_type]
                    if visonic_id not in valid_dict:
                        self.logger.logstate_debug("Deleting entity from HA %s", uid)
                        created_list.discard(visonic_id)
                        device_id = entity.device_id
                        entity_reg.async_remove(entity.entity_id)
                        if device_id is not None:
                            remaining = [
                                e for e in er.async_entries_for_device(entity_reg, device_id)
                                if e.entity_id != entity.entity_id
                            ]
                            if not remaining:
                                self.logger.logstate_debug("Deleting empty device from HA %s", device_id)
                                device_reg.async_remove_device(device_id)

    def _delete_from_device_registry(self, identifiers) -> bool:
        device_registry = dr.async_get(self.hass)
        # delete
        retval = False
        dev = device_registry.async_get_device(identifiers=identifiers)
        if dev:
            device_registry.async_remove_device(dev.id)
            retval = True
        else:
            self.logger.logstate_debug("Sensor %s not deleted", identifiers)
        self.rationalise_ha_devices(True)
        return retval

    def create_image_entity(self, zsd: ZoneSensorData):
        """Create an image entity for a camera sensor."""
        # The issue is that PIR Sensors could be detected and created without knowing that it's a Camera PIR Sensor until too late
        # We might not know the sensor type when we first startup, could be standard mode or whatever
        #self.logger.logstate_debug("Adding Sensor Image %s", zsd.device_id)
        if zsd.device_id not in self._image_created_set:
            self._image_created_set.add(zsd.device_id)
            # The connection to the panel allows interaction with the sensor, including asking to get the image from a camera
            self.setup_visonic_entity(Platform.IMAGE, zsd)
            self.setup_visonic_entity(Platform.BUTTON, zsd)

    def sensor_create_entities(self, sensor: SensorState, identifier: str):
        """Create sensor entities."""
        # Create entities attached to the device, binary_sensor, select and image
        binary_entities = []
        float_entities = []

        for entity in sensor.sensor_type.entities:
            ent, timeout = entity
            if isinstance(ent, VisonicFloatSensorKey):
                float_entities.append(FloatSensorData(identifier=identifier, device_id=sensor.id, sensor_definition=ent, initial_state=None))
            elif isinstance(ent, VisonicBinarySensorKey):
                binary_entities.append(BinarySensorData(identifier=identifier, device_id=sensor.id, sensor_definition=ent, initial_state=None, timeout_type=timeout))

        self.setup_visonic_entity(Platform.BINARY_SENSOR, binary_entities)
        if len(float_entities) > 0:
            self.setup_visonic_entity(Platform.SENSOR, float_entities)

        # If master_include_bypass and the user has allowed sensors to be bypassed, then create select entities
        esb = to_bool(self.entry.options.get(CONF_ENABLE_SENSOR_BYPASS, False))
        if esb and not self.force_standard_mode:
            # The connection to the panel allows interaction with the sensor, including the arming/bypass of the sensors
            zsd = ZoneSensorData(identifier=identifier, device_id=sensor.id)
            self.setup_visonic_entity(Platform.SELECT, zsd)

    def sensor_update_or_create(
        self,
        sensor: SensorState,
    ) -> bool:
        """Create new Sensor."""
        if sensor is None or sensor.id is None:
            return False
        if sensor.id not in self.exclude_sensor_list:
            identifier = create_sensor_unique_id(self.panel_ident, sensor.id)
            if sensor.id not in self._sensor_dict:
                # Create
                d = create_sensor_label(sensor.id)
                identifiers = {(DOMAIN, identifier)}
                device_registry = dr.async_get(self.hass)
                s = f"{sensor.sensor_type.name} Sensor"
                n = (
                    f"Visonic {d}"
                    if self.panel_ident == 0
                    else f"Visonic P{self.panel_ident} {d}"
                )

                # Look up the area and assign the area to the sensor
                area_reg = ar.async_get(self.hass)
                area_map = {
                    area.id: area.name
                    for area in area_reg.async_list_areas()
                }
                suggested_area: str | UndefinedType | None = UNDEFINED # set to default for async_get_or_create
                loc0 = sensor.location[0].casefold() if sensor.location is not None else "area_undefined_so_do_not_match_the_area"
                loc1 = sensor.location[1].casefold() if sensor.location is not None else "area_undefined_so_do_not_match_the_area"
                for areavalue in area_map.values():
                    # casefold is similar to lower but can be used with different languages
                    if loc0 == areavalue.casefold() or loc1 == areavalue.casefold():
                        suggested_area = areavalue
                        break

                # get/create the device registry entry for the sensor
                _dev = device_registry.async_get_or_create(
                    config_entry_id=self.entry.entry_id,
                    identifiers=identifiers,
                    name=n,
                    manufacturer=MANUFACTURER,
                    model=s.title().replace("_", " "),
                    suggested_area=suggested_area,
                    model_id=sensor.sensor_type.name,
                )

                self.logger.logstate_debug("Adding Sensor identifier %s with name %s", identifier, n)
                self._sensor_dict[sensor.id] = sensor
                if sensor.id not in self._sensor_created_set:
                    self._sensor_created_set.add(sensor.id)
                    # Create the sensor entities except the image entity
                    self.sensor_create_entities(sensor, identifier)

            if sensor.id in self._sensor_dict:
                # update
                self._sensor_dict[sensor.id] = sensor
                if (
                    not self.disable_all_panel_commands
                    and sensor.id not in self._image_created_set
                    and sensor.sensor_type.type == AlarmSensorType.CAMERA
                ):
                    # Create the image entity as required for the camera
                    zsd = ZoneSensorData(identifier=identifier, device_id=sensor.id)
                    self.create_image_entity(zsd)
                    if not self.created_download_active_sensor:
                        self.created_download_active_sensor = True
                        puid = getAlarmPanelUniqueIdent(self.panel_ident)
                        self.setup_visonic_entity(Platform.BINARY_SENSOR, BinaryImageDownloadData(identifier=puid))

                if sensor.has_image and sensor.image_data is not None:
                    self.image_manager.set_sensor_jpeg(sensor.id, sensor.image_data, sensor.image_is_audio)

            return True
        self.logger.logstate_debug("Sensor %s in exclusion list or None", sensor.id)
        return False

    def delete_sensor(
        self,
        sid: int,
    ) -> bool:
        """Delete Sensor."""
        if sid is not None and sid in self._sensor_dict:
            identifier = create_sensor_unique_id(self.panel_ident, sid)
            identifiers = {(DOMAIN, identifier)}
            self._delete_from_device_registry(identifiers=identifiers)
            self._sensor_dict.pop(sid, None)
            self.image_manager.delete_all_sensor_jpeg(sid)
            self.logger.logstate_debug("Sensor %s to be deleted, also need to delete the select entity if it was created", sid)
            return True
        self.logger.logstate_debug("Sensor %s in exclusion list or None", sid)
        return False

    def switch_update_or_create(
        self,
        switch: SwitchState,
    ) -> bool:
        """Create new Switch."""
        if switch is None or switch.id is None:
            return False
        if switch.id not in self.exclude_switch_list:
            if switch.id not in self._switch_dict:
                # Create
                identifier = create_switch_unique_id(self.panel_ident, switch.id)
                d = create_switch_label(switch.id)
                identifiers = {(DOMAIN, identifier)}
                device_registry = dr.async_get(self.hass)
                n = (
                    f"Visonic {d}"
                    if self.panel_ident == 0
                    else f"Visonic P{self.panel_ident} {d}"
                )
                _dev = device_registry.async_get_or_create(
                    config_entry_id=self.entry.entry_id,
                    identifiers=identifiers,
                    name=n,
                    manufacturer=MANUFACTURER,
                    model=(
                        switch.model.replace("_", " ") if switch.model is not None else "Unknown"
                    ),
                )
                #self.logger.logstate_debug(f"Adding Switch {switch.id=}")
                self.logger.logstate_debug("Adding Switch identifier %s with name %s", identifier, n)
                self._switch_dict[switch.id] = switch
                if switch.id not in self._switch_created_set:
                    self._switch_created_set.add(switch.id)
                    self.setup_visonic_entity(
                        Platform.SWITCH,
                        ZoneSensorData(identifier=identifier, device_id=switch.id)
                    )
            if switch.id in self._switch_dict:
                # update
                self._switch_dict[switch.id] = switch
            return True
        self.logger.logstate_debug("Switch %s in exclusion list", switch.id)
        return False

    def delete_switch(
        self,
        sid: int,
    ) -> bool:
        """Delete Sensor."""
        if sid is not None and sid in self._switch_dict:
            identifier = create_switch_unique_id(self.panel_ident, sid)
            identifiers = {(DOMAIN, identifier)}
            self._delete_from_device_registry(identifiers=identifiers)
            self._switch_dict.pop(sid, None)
            self.logger.logstate_debug("Switch %s deleted", sid)
            return True
        self.logger.logstate_debug("Switch %s in exclusion list or None", sid)
        return False

    def create_device_type(self, device: DeviceState) -> str:
        """Create a device name string."""
        dt = device.device_type.lower()
        mapping = {
            "fob": "KF",
            "pad1": "KP",
            "pad2": "KT",
            "siren": "S",
        }
        for needle, prefix in mapping.items():
            if needle in dt:
                return f"{prefix}"
        return "DU"

    def device_update_or_create(
        self,
        device: DeviceState,
    ) -> bool:
        """Create new Device."""
        # cannot do anything with "device" other than assign it to self._device_dict[did]
        # if not create (delete) then device does not need to be passed in
        if device is None or device.id is None:
            return False
        if device.id not in self._device_dict:
            # Create
            prefix = self.create_device_type(device)
            identifier = create_device_unique_id(self.panel_ident, prefix, device.id)
            d = create_device_label(prefix, device.id)
            identifiers = {(DOMAIN, identifier)}
            device_registry = dr.async_get(self.hass)
            n = (
                f"Visonic {d}"
                if self.panel_ident == 0
                else f"Visonic P{self.panel_ident} {d}"
            )
            _dev = device_registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers=identifiers,
                name=n,
                manufacturer=MANUFACTURER,
                model=(
                    device.model.replace("_", " ") if device.model is not None else "Unknown"
                ),
            )
            #self.logger.logstate_debug(f"Adding {device.name} {device.id}")
            self.logger.logstate_debug("Adding Device identifier %s with name %s", identifier, n)
            self._device_dict[device.id] = device
            if device.id not in self._device_created_set:
                self._device_created_set.add(device.id)
                self.setup_visonic_entity(
                    Platform.BINARY_SENSOR,
                    BinarySensorData(identifier=identifier, device_id=device.id, sensor_definition=VisonicBinarySensorKey.DEVICE_BATTERY, initial_state=None, timeout_type=SensorOnTimeout.NO_TIMEOUT),
                )
        if device.id in self._device_dict:
            self._device_dict[device.id] = device
            return True
        return False

    def delete_device(
        self,
        sid: int,
    ) -> bool:
        """Delete Sensor."""
        if sid is not None and sid in self._device_dict:
            device = self._device_dict[sid]
            identifier = create_device_unique_id(self.panel_ident, self.create_device_type(device), device)
            identifiers = {(DOMAIN, identifier)}
            self._delete_from_device_registry(identifiers=identifiers)
            self._device_dict.pop(sid, None)
            self.logger.logstate_debug("Device %s deleted", sid)
            return True
        return False

    def terminate_all_dispatchers(self, entry: VisonicConfigEntry):
        """Kill all the dispatchers for this entry."""
        for p in PLATFORMS:
            if d := entry.runtime_data.dispatchers.get(p):
                d()
                entry.runtime_data.dispatchers.pop(p, None)
                self.logger.logstate_debug("[terminate_all_dispatchers]  %s  Success", p)
            else:
                self.logger.logstate_info("[terminate_all_dispatchers]  %s  Not Done", p)
        # Reset the run time data parameters, keep client (and the dispatchers are all kept but set to None above)
        entry.runtime_data.alarm_entity = None
        # entry.runtime_data.sensors = []

    def get_sensors_to_bypass(self, parts: int | set[int] | None) -> set[int]:
        """Determine the list of sensors that are open and not already bypassed, in order to arm the panel."""
        if isinstance(parts, int):
            parts = None if parts == PARTITION_ID_WHEN_BASE else [parts]
        return (
            {
                sid
                for p in parts
                for sid, s in self._sensor_dict.items()
                if p in s.partition and not s.bypass and s.status
            }
            if parts
            else {
                sid
                for sid, s in self._sensor_dict.items()
                if not s.bypass and s.status
            }
        )

    def populateSensorDictionary(self) -> dict[str, Any]:
        """Create the sensor dict."""
        datadict: dict[str, list[str]] = {}
        datadict["open"] = []
        datadict["bypass"] = []
        datadict["tamper"] = []
        datadict["zonetamper"] = []

        base = Platform.BINARY_SENSOR + "."
        for sid, sensor in self._sensor_dict.items():
            entname = create_sensor_unique_id(self.panel_ident, sid)
            if sensor.status:
                datadict["open"].append(base + entname)
            if sensor.bypass:
                datadict["bypass"].append(base + entname)
            if sensor.tamper:
                datadict["tamper"].append(base + entname)
            if sensor.zonetamper:
                datadict["zonetamper"].append(base + entname)
        return datadict

    async def async_get_zone_switch_info(self, valid: bool) -> dict[str, Any]:
        """Service call get open zones in the panel."""
        # Create a dictionary of name, sensor   from  id, sensor
        sensors_info = [
            (Platform.BINARY_SENSOR + "." + create_sensor_unique_id(self.panel_ident, sid), sensor)
            for sid, sensor in self._sensor_dict.items()
        ]
        return {
            "valid": valid,
            "sensors": [ent for ent, _ in sensors_info if valid],
            "batterylow": [ent for ent, s in sensors_info if valid and s.low_battery],
            "open": [ent for ent, s in sensors_info if valid and s.status],
            "bypass": [ent for ent, s in sensors_info if valid and s.bypass],
            "switches": [
                Platform.SWITCH + "." + create_switch_unique_id(self.panel_ident, sid)
                for sid in self._switch_dict
                if valid
            ],
        }

    def sensor_state(self) -> dict[int, SensorState]:
        """Return a dict of all the sensors as_dict."""
        return self._sensor_dict

    def switch_state(self) -> dict[int, SwitchState]:
        """Return a dict of all the sensors as_dict."""
        return self._switch_dict

    def device_state(self) -> dict[int, DeviceState]:
        """Return a dict of all the sensors as_dict."""
        return self._device_dict
