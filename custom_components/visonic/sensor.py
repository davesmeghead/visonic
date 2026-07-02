"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System and Create a Simple Entity to Report Status only."""

import logging
from enum import IntEnum
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorEntity, SensorDeviceClass
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.components.alarm_control_panel import AlarmControlPanelState

# Use the standard HA core attributes, alarm states and services to report status
from homeassistant.const import (
#    STATE_ALARM_ARMED_AWAY,
#    STATE_ALARM_ARMED_HOME,
#    STATE_ALARM_ARMING,
#    STATE_ALARM_DISARMED,
#    STATE_ALARM_PENDING,
#    STATE_ALARM_TRIGGERED,
    STATE_UNKNOWN,
    PERCENTAGE,
    EntityCategory,
)

from .client import VisonicClient
from . import VisonicConfigEntry
from .const import (
    DOMAIN,
    VISONIC_TRANSLATION_KEY,
    map_panel_status_to_ha_status,
    MANUFACTURER,
    TEXT_LAST_EVENT_NAME,
    PANEL_ATTRIBUTE_NAME,
    DEVICE_ATTRIBUTE_NAME,
)

from .pyconst import AlPanelStatus, AlSensorType, AlSensorDevice

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Visonic Alarm Sensors for Monitor."""
    #_LOGGER.debug(f"[async_setup_entry] start")

    @callback
    def async_add_sensor(main_one : bool = False) -> None:
        """Add Visonic Sensor (to behave instead of the alarm panel when all comms is prevented)."""
        entities: list[Entity] = []
        client: VisonicClient = entry.runtime_data.client

        p = client.getPartitionsInUse()

        if main_one and entry.runtime_data.alarm_entity is None: #  or p is None or (p is not None and len(p) == 1):
            entry.runtime_data.alarm_entity = VisonicSensor(hass, client)
            entities.append(entry.runtime_data.alarm_entity)
            _LOGGER.debug(f"[async_setup_entry] adding main entity for panel {client.getPanelID()}")
        elif entry.runtime_data.alarm_entity is not None and p is not None and len(p) > 1:
            _LOGGER.debug(f"[async_setup_entry] updating main alarm control panel entity for partition set {p}")
            entry.runtime_data.alarm_entity.resetPartition(0)
            for i in p:
                if i != 0:
                    entities.append(VisonicSensor(hass, client, i))
            _LOGGER.debug(f"[async_setup_entry] adding sensor panel entities for partition set {p}")

        if len(entities) > 0:
            async_add_entities(entities, True)

    entry.runtime_data.dispatchers[SENSOR_DOMAIN] = async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_add_{SENSOR_DOMAIN}", async_add_sensor)

    @callback
    def async_add_battery(device: AlSensorDevice) -> None:
        """Add a battery sensor for a zone device."""
        if device is None or device.getSensorType() == AlSensorType.WIRED:   # wired zones have no battery
            return
        async_add_entities([VisonicBatterySensor(hass, entry.runtime_data.client, device)])

    # Reuse the per-device binary_sensor dispatch signal to also create a battery sensor per device
    entry.runtime_data.dispatchers[SENSOR_DOMAIN + "_battery"] = async_dispatcher_connect(
        hass, f"{DOMAIN}_{entry.entry_id}_add_{BINARY_SENSOR_DOMAIN}", async_add_battery
    )
    #_LOGGER.debug("[async_setup_entry] exit")

class VisonicSensor(Entity):
    """Representation of a Visonic alarm control panel as a simple sensor for minimal."""

    def __init__(self, hass: HomeAssistant, client: VisonicClient, partition : int = None):
        """Initialize a Visonic security alarm."""
        self._client = client
        self.hass = hass
        self._attr_state = STATE_UNKNOWN
        self._last_triggered = ""
        self.resetPartition(partition)
        self._client.onChange(callback = self.onClientChange)
        #_LOGGER.debug(f"[VisonicSensor] Initialising alarm sensor {self._myname}")
        self._attr_unique_id = slugify(self._myname+"_sensor")
        self._attr_name = "Alarm Panel" # self._name
        self._attr_translation_key = VISONIC_TRANSLATION_KEY

    def resetPartition(self, partition : int | None):
        if partition is None:
            self._partition = None           # When partitions are not used then we only use partition 1 for panel state
            self._partitionSet = {1, 2, 3}   # When partitions are not used then we command (Arm, Disarm etc) all partitions
            self._myname = self._client.getAlarmPanelUniqueIdent()
            _LOGGER.debug(f"[VisonicAlarm] Setting primary sensor {self._myname}      {self.unique_id=}")
        elif partition == 0:                 # EXPERIMENTAL
            self._partition = 0              # When partitions are not used then we only use partition 0 for panel state
            self._partitionSet = {1, 2, 3}   # When partitions are not used then we command (Arm, Disarm etc) all partitions
            self._myname = self._client.getAlarmPanelUniqueIdent()
            _LOGGER.debug(f"[VisonicAlarm] Setting sensor {self._myname}      {self.unique_id=}")
        else:
            self._partition = partition
            self._partitionSet = { partition }
            self._myname = self._client.getAlarmPanelUniqueIdent() + " Partition " + str(partition)
            _LOGGER.debug(f"[VisonicAlarm] Setting alarm sensor {self._myname}      {self.unique_id=}")
        self._client.setPartitionNaming(partition = partition, panel_entity_name = self._myname)
        pm = self._client.getPanelModel()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, {(DOMAIN, self._client.getAlarmPanelUniqueIdent())})},
            model = pm,
        )

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        _LOGGER.debug(f"[async_will_remove_from_hass] Removing alarm panel sensor {self._myname} panel {self._client.getPanelID()}")
        self._client = None
        await super().async_will_remove_from_hass()

    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up or have been removed then assume we need a valid code
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    # The callback handler from the client. All we need to do is schedule an update.
    def onClientChange(self):
        """HA Event Callback."""
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    @property
    def changed_by(self):
        """Last change triggered by."""
        return self._last_triggered

    @property
    def has_entity_name(self) -> bool:
        """Prevent HA adding the device name to the start of the entity name."""
        return False

    def update(self):
        """Get the state of the device."""
        #_LOGGER.debug(f"[update] before {self._attr_state=}")
        self._attr_state = STATE_UNKNOWN
        self._attr_extra_state_attributes = {}

        if self._client is not None and self.isPanelConnected():
            ptu = self._client.getPartitionsInUse()
            isa, _ = self._client.isSirenActive(None if ptu is None else 0)
            if isa:
                self._attr_state = AlarmControlPanelState.TRIGGERED
            else:
                armcode = self._client.getPanelStatus(self._partition)
                if armcode is not None and armcode in map_panel_status_to_ha_status:
                    self._attr_state = map_panel_status_to_ha_status[armcode]

            stat = self._client.getPanelStatusDict(self._partition)
            #_LOGGER.debug(f"[update] stat {stat}")

            data = None
            if self._partition is None or self._partition == 0:
                data = self._client.getClientStatusDict()
                if TEXT_LAST_EVENT_NAME in stat and len(stat[TEXT_LAST_EVENT_NAME]) > 2:
                    self._last_triggered = stat[TEXT_LAST_EVENT_NAME]

            if data is not None and stat is not None:
                self._attr_extra_state_attributes = {**stat, **data}
            elif stat is not None:
                self._attr_extra_state_attributes = stat
            elif data is not None:
                self._attr_extra_state_attributes = data
            
            self._attr_extra_state_attributes[PANEL_ATTRIBUTE_NAME] = self._client.getPanelID()
            #_LOGGER.debug(f"[update] _attr_extra_state_attributes {self._attr_extra_state_attributes=}")

        #_LOGGER.debug(f"[update] after {self._attr_state=}")


class VisonicBatterySensor(SensorEntity):
    """A battery level sensor for a Visonic zone device.

    The panel only reports a low-battery flag (not a true percentage), so this
    reports 100% normally and 10% when the device signals a low battery, which
    is enough to drive Home Assistant low-battery alerts.
    """

    _attr_translation_key: str = VISONIC_TRANSLATION_KEY
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, client: VisonicClient, sensor: AlSensorDevice):
        """Initialize the battery sensor."""
        self.hass = hass
        self._client = client
        self._visonic_device = sensor
        self._dname = sensor.createFriendlyName()
        pname = client.getMyString()
        # Match VisonicBinarySensor._name so device_info links to the same HA device
        self._name = pname.lower() + self._dname.lower()
        self._panel = client.getPanelID()
        self._is_available = sensor.isEnrolled()
        sensor.onChange(self.onChange)

    def onChange(self, sensor: AlSensorDevice = None, s=None):
        """Call on any change to the sensor."""
        if self._visonic_device is not None:
            self._is_available = self._visonic_device.isEnrolled()
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state()

    async def async_will_remove_from_hass(self):
        """Remove from hass. Do not clear the shared device callback list here."""
        self._visonic_device = None
        self._client = None
        self._is_available = False
        await super().async_will_remove_from_hass()

    @property
    def should_poll(self):
        return False

    @property
    def unique_id(self) -> str:
        return slugify(self._name + "_battery")

    @property
    def name(self):
        return self._name + " Battery"

    @property
    def available(self) -> bool:
        return self._is_available

    @property
    def device_info(self):
        """Link this entity to the same device as the zone binary sensor."""
        return {"identifiers": {(DOMAIN, slugify(self._name))}}

    @property
    def native_value(self):
        """Return 100% normally, 10% when a low battery is signalled."""
        if self._visonic_device is None:
            return None
        return 10 if self._visonic_device.isLowBattery() else 100

    @property
    def extra_state_attributes(self):
        d = self._visonic_device
        if d is None:
            return {}
        return {
            "low_battery": d.isLowBattery(),
            DEVICE_ATTRIBUTE_NAME: d.getDeviceID(),
            PANEL_ATTRIBUTE_NAME: self._panel,
        }
