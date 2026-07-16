"""Global Types."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum, IntFlag, StrEnum, auto
from functools import partial
import logging
from types import MappingProxyType
from typing import Any, Protocol, Self

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription
from homeassistant.components.sensor.const import SensorStateClass
from homeassistant.const import (
    ATTR_ARMED,
    ATTR_BATTERY_LEVEL,
    LIGHT_LUX,
    EntityCategory,
    UnitOfTemperature,
)

from .const import DEVICE_ATTRIBUTE_NAME, VISONIC_TRANSLATION_KEY
from .utils import create_sensor_label, print_partition
from .visonic_types import AlarmSensorType

_LOGGER = logging.getLogger(__name__)

###################################################################################
############## Sensor Definitions for binary_sensor and sensor (for float) ########
############## State Classes: Panel, Sensor, Switch and Device ####################
###################################################################################


# Dictionary mapping between the sensor type and the HA Sensor Class
STYPE_TO_HA_SENSOR_MAP: dict[AlarmSensorType, BinarySensorDeviceClass | None] = {
    AlarmSensorType.IGNORED: None,
    AlarmSensorType.UNKNOWN: None,
    AlarmSensorType.MOTION: BinarySensorDeviceClass.MOTION,
    AlarmSensorType.CAMERA: BinarySensorDeviceClass.MOTION,
    AlarmSensorType.MAGNET: BinarySensorDeviceClass.WINDOW,
    AlarmSensorType.WIRED: BinarySensorDeviceClass.DOOR,
    AlarmSensorType.SMOKE: BinarySensorDeviceClass.SMOKE,
    AlarmSensorType.FLOOD: BinarySensorDeviceClass.MOISTURE,
    AlarmSensorType.GAS: BinarySensorDeviceClass.GAS,
    AlarmSensorType.VIB: BinarySensorDeviceClass.VIBRATION,
    AlarmSensorType.SHOCK: BinarySensorDeviceClass.VIBRATION,
    AlarmSensorType.TEMP: BinarySensorDeviceClass.HEAT,
    AlarmSensorType.SOUND: BinarySensorDeviceClass.SOUND,
    AlarmSensorType.GLASS: BinarySensorDeviceClass.VIBRATION,
    AlarmSensorType.PANEL: BinarySensorDeviceClass.RUNNING,
    AlarmSensorType.COMMS: BinarySensorDeviceClass.CONNECTIVITY,
    AlarmSensorType.TOKEN: None,
    AlarmSensorType.SIREN: BinarySensorDeviceClass.RUNNING,
    AlarmSensorType.SWITCH: BinarySensorDeviceClass.OPENING,
}

class VisonicBinarySensorKey(StrEnum):
    """Keys for the BINARY_SENSOR_DEFINITIONS."""
    # I could have separated them for float and binary but ... I didn't
    ZONE_TRIGGER = "zone_trigger"
    ZONE_STATUS = "zone_status"
    ZONE_CONTACT = "zone_contact"
    ZONE_BATTERY = "zone_battery"
    DEVICE_BATTERY = "device_battery"
    PANEL_BATTERY = "panel_battery"
    ZONE_TAMPER = "zone_tamper"
    PANEL_TAMPER = "panel_tamper"
    ZONE_PROBLEM = "zone_problem"
    PANEL_PROBLEM = "panel_problem"
    ZONE_MISSING = "zone_missing"
    ZONE_ONEWAY = "zone_oneway"
    ZONE_INACTIVE = "zone_inactive"

class VisonicFloatSensorKey(StrEnum):
    """Keys for the FLOAT_SENSOR_DEFINITIONS."""
    ZONE_TEMP = "zone_temp"
    ZONE_LUX = "zone_lux"

class SensorOnTimeout(Enum): # Short for sensor class but abbreviated as used in the table a lot
    """Which timeout the trigger sensor uses."""
    NO_TIMEOUT = auto() # Do nothing
    MOTION = auto()     # Create an entity and use motion timeout
    STATE = auto()      # Create an entity and use magnet/state timeout
    OTHER = auto()      # Create an entity and use other timeout

@dataclass(slots=True, frozen=True)
class ZoneSensorDetails:
    """Visonic Zone Sensor Type Definition."""
    # Default to a simple state based sensor.
    name: str = "Unknown"
    type: AlarmSensorType = AlarmSensorType.UNKNOWN    # AlarmSensorType Enum
    entities: list[tuple[VisonicBinarySensorKey, SensorOnTimeout]] = field(default_factory=list)

class DataclassDictMixin:
    """Base class for dictionary conversions."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an instance from a dictionary."""
        field_names = {f.name for f in fields(cls)}
        kwargs = {
            k: v
            for k, v in data.items()
            if k in field_names
        }
        # Development diagnostics
        # unused_keys = set(data) - field_names
        # missing_fields = field_names - set(kwargs)
        # if unused_keys:
        #     _LOGGER.warning(
        #         "%s: unused input keys = %s",
        #         cls.__name__,
        #         sorted(unused_keys),
        #     )
        # if missing_fields:
        #     _LOGGER.warning(
        #         "%s: fields using defaults = %s",
        #         cls.__name__,
        #         sorted(missing_fields),
        #     )
        return cls(**kwargs)

    def as_dict(self) -> dict[str, Any]:
        """Convert to a dict and flatten dict-valued fields."""
        data: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, dict):
                data.update(value)
            else:
                data[f.name] = value
        return data

@dataclass(slots=True)
class PanelState(DataclassDictMixin):
    """Internal panel state."""
    emulationmode: str
    trouble: str
    battery_level: int
    tamper: bool
    partition: list[int] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True, kw_only=True, frozen=True)
class BaseStateClass(DataclassDictMixin):
    """Common base class for sensor, switch and device state."""
    id: int

@dataclass(slots=True, kw_only=True, frozen=True)
class SensorState(BaseStateClass):
    """State of each sensor to pass to coordinator data."""
    problem: str = ""
    sensor_type_id: int
    partition: set[int] = field(default_factory=set)
    location: tuple[str, str] = ("", "")
    zonetype: str = ""
    chime: str | None = None
    bypass: bool = False
    low_battery: bool = False
    status: bool = False
    tamper: bool = False
    enrolled: bool = False
    triggered: bool = False
    zonetamper: bool = False
    temperature: float | None = None
    luminance: float | None = None
    ismissing: bool | None = None
    isoneway: bool | None = None
    isinactive: bool | None = None
    has_image: bool = False
    image_time: datetime | None = None
    time: datetime | None = None
    sensor_type: ZoneSensorDetails = field(default_factory=ZoneSensorDetails)

@dataclass(slots=True, kw_only=True, frozen=True)
class SwitchState(BaseStateClass):
    """Internal switch state."""
    status: bool = False
    enabled: bool = False
    model: str = ""
    location: str = ""

@dataclass(slots=True, kw_only=True, frozen=True)
class DeviceState(BaseStateClass):
    """Internal device state."""
    device_type: str = ""
    enabled: bool = False
    name: str = ""
    model: str = ""
    location: str = ""
    state: bool = False
    low_battery: bool = False
    trouble: str = ""
    tamper: bool = False
    bypass: bool = False
    partitions: set[int] = field(default_factory=set)

@dataclass(slots=True)
class BaseData:
    """Base Sensor Data."""
    # This is the device identifier returned in the DeviceInfo to group sensors under a device
    identifier: str

@dataclass(slots=True)
class AlarmPanelData(BaseData):
    """Base Sensor Data."""
    partitions: set[int]
    siren_id: int
    siren_name: str

    def __str__(self) -> str:  # noqa: D105
        parts = [
            f"identifier={self.identifier!r}",
            f"siren={self.siren_name!r} ({self.siren_id})",
        ]
        if self.partitions:
            p = [s+1 for s in self.partitions]
            parts.append(f"partitions={', '.join(map(str, sorted(p)))}")
        return ", ".join(parts)

@dataclass(slots=True)
class ZoneSensorData(BaseData):
    """ZoneSensorData Sensor Data."""
    device_id: int

@dataclass(slots=True)
class FloatSensorData(BaseData):
    """Float / Integer values, Sensor Data."""
    device_id: int
    sensor_definition: VisonicFloatSensorKey
    initial_state: float

@dataclass(slots=True)
class BinarySensorData(BaseData):
    """Binary (boolean) Sensor Data."""
    device_id: int
    sensor_definition: VisonicBinarySensorKey
    initial_state: bool
    timeout_type: SensorOnTimeout

#@dataclass(slots=True)
#class StringSensorData(ZoneSensorData):
#    """String Sensor Data."""
#    sensor_definition: VisonicBinarySensorKey
#    initial_state: str

class EntityDataType(StrEnum):
    """Source data type."""
    PANEL = "panel"     # use the "panelstate" from coordinator data
    ZONE = "zones"      # use the "zones" coordinator data
    SWITCH = "switch"   # use the "switch" coordinator data
    DEVICE = "device"   # use the "device" coordinator data

class AttributesFn(Protocol):
    """Make a type defn protocol."""
    def __call__(self, sensor_data: Mapping[str, Any] | SensorState | DeviceState | PanelState) -> dict[str, Any]: ...  # noqa: D102
    """Attributes Function Protocol."""

class ValueFn(Protocol):
    """Make a type defn protocol."""
    def __call__(self, sensor_data: bool | float | str) -> bool | float | str | None: ...  # noqa: D102
    """Value Function Protocol."""

class StateField(StrEnum):
    """Attribute names used to obtain state from coordinator data."""
    # Zone
    TRIGGERED = "triggered"
    STATUS = "status"
    LOW_BATTERY = "low_battery"
    ZONETAMPER = "zonetamper"
    PROBLEM = "problem"
    ISMISSING = "ismissing"
    ISONEWAY = "isoneway"
    ISINACTIVE = "isinactive"
    TEMPERATURE = "temperature"
    LUMINANCE = "luminance"
    # Panel
    BATTERY_LEVEL = "battery_level"
    TAMPER = "tamper"
    TROUBLE = "trouble"
    # Device
    ENABLED = "enabled"

@dataclass(frozen=True, kw_only=True)
class VisonicSensorDefinition:
    """Entity Data definition for creation and data access in coordinator.data."""
    source: EntityDataType         # Identify which dict to use from coordinator data
    data_key: StateField           # This is the key in the dict that is used for the state
    unique_extension: str = ""
    friendly_name: str | None = None
    value_fn: ValueFn
    #attributes_fn: Callable[[Mapping[str, Any] | SensorState | DeviceState | PanelState], dict[str, Any]]
    attributes_fn: AttributesFn

@dataclass(frozen=True, kw_only=True)
class BinarySensorDefinition(VisonicSensorDefinition, BinarySensorEntityDescription):
    """Binary Entity Data definition for creation and data access in coordinator.data."""

@dataclass(frozen=True, kw_only=True)
class FloatSensorDefinition(VisonicSensorDefinition, SensorEntityDescription):
    """Float Entity Data definition for creation and data access in coordinator.data."""


def evaluate_binary_state(s: bool | float | str | None, invert: bool, match_value : str | None = None) -> bool | None:
    """Calculate a boolean/binary state from the inputs."""
    if s is None:
        return None
    if isinstance(s, bool):
        return not s if invert else s  # On means low, Off means normal
    if isinstance(s, (int, float)):
        return bool(s >= 50.0) if invert else bool(s < 50.0)  # On means low, Off means normal
    if isinstance(s, str):
        val = s != match_value
        return not val if invert else val
    return None

def evaluate_float_state(s: float | str | None, invert: bool) -> float | None:
    """Calculate a floating point value from the inputs."""
    if s is None:
        return None
    try:
        # Attempt conversion
        return float(-s) if invert else float(s)
    except (TypeError, ValueError):
        # Handle conversion failure safely
        _LOGGER.warning(f"Error: '{s}' cannot be converted to a float.")  # noqa: G004
        return None

evaluate_float_normal = partial(
    evaluate_float_state,
    invert=False
)

evaluate_binary_state_normal = partial(
    evaluate_binary_state,
    invert=False
)

evaluate_binary_state_invert = partial(
    evaluate_binary_state,
    invert=True
)

def evaluate_trouble(value):
    """Evaluate the trouble attribute."""
    return evaluate_binary_state(value, False, "none")

UNSET = "UNSET"

def obtain_attributes(
    obj: object,
    #sensor: Any,        # Ignored, here to match sensor_full_attributes
    lst: Iterable[str],
) -> dict[str, Any]:
    """Get selected attributes from an object."""
    retval: dict[str, Any] = {}
    extra_attrs = getattr(obj, "attributes", {})
    for attr in lst:
        value = getattr(obj, attr, UNSET)
        if value is not UNSET:
            retval[attr] = value
        elif isinstance(extra_attrs, dict) and attr in extra_attrs:
            retval[attr] = extra_attrs[attr]
    return retval

device_basic_attributes = partial(
    obtain_attributes,
    lst=["enabled"]
)

panel_trouble_attributes = partial(
    obtain_attributes,
    lst=["trouble", "lasteventname", "lasteventaction", "lasteventpartition", "lasteventtime"]
)

def sensor_subset_attributes(
    sensor: SensorState,
    lst: list[str],
) -> dict[str, Any]:
    """Get selected attributes from an object."""
    attr: dict[str, Any] = {}
    all_attrs = sensor_full_attributes(sensor)
    ll = [DEVICE_ATTRIBUTE_NAME, "device_name", "partition"]
    ll.extend(lst)
    for a in ll:
        if a in all_attrs:
            attr[a] = all_attrs[a]
    for k,v in attr.items():
        if v is None:
            attr[k] = "none"
    return attr


def sensor_full_attributes(
    sensor: SensorState,
) -> dict[str, Any]:
    """Get selected attributes from an object."""
    attr: dict[str, Any] = {}
    attr[DEVICE_ATTRIBUTE_NAME] = sensor.id
    attr["device_tamper"] = sensor.tamper
    attr["zone_tamper"] = sensor.zonetamper
    attr[ATTR_ARMED] = not sensor.bypass

    zn = sensor.location
    if len(zn) == 2:
        attr["zone_name"] = zn[0]
        attr["zone_name_panel"] = "Unknown" if zn[1] is None else zn[1]

    attr["zone_type"] = sensor.zonetype
    attr["zone_chime"] = sensor.chime
    attr["zone_trouble"] = sensor.problem

    if sensor.sensor_type.type != AlarmSensorType.UNKNOWN:
        attr["sensor_type"] = str(sensor.sensor_type.type.name).lower()
    elif sensor.sensor_type_id is not None:
        attr["sensor_type"] = "Undefined " + str(sensor.sensor_type_id)
    else:
        attr["sensor_type"] = "unknown"

    if sensor.ismissing is not None:
        attr["zone_missing"] = sensor.ismissing
    if sensor.isoneway is not None:
        attr["zone_oneway"] = sensor.isoneway
    if sensor.isinactive is not None:
        attr["zone_inactive"] = sensor.isinactive
    if sensor.luminance is not None:
        attr["zone_lux"] = sensor.luminance
    if sensor.temperature is not None:
        attr["zone_temperature"] = sensor.temperature

    if sensor.sensor_type.type != AlarmSensorType.WIRED:
        attr[ATTR_BATTERY_LEVEL] = 0 if sensor.low_battery else 100

    if len(sensor.partition) > 0:
        attr["partition"] = print_partition(sensor.partition)

    attr["device_name"] = create_sensor_label(sensor.id)

    for k,v in attr.items():
        if v is None:
            attr[k] = "none"

    return attr


def empty_attributes(_):  # noqa: D103
    return {}

FLOAT_SENSOR_DEFINITIONS: Mapping[
    VisonicFloatSensorKey,
    FloatSensorDefinition,
] = MappingProxyType( {
    VisonicFloatSensorKey.ZONE_TEMP: FloatSensorDefinition(
        key="zone_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        source=EntityDataType.ZONE, # use the "zones"
        data_key=StateField.TEMPERATURE,        # use sensor.temperature
        unique_extension="_temp",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_float_normal,
        attributes_fn=partial(sensor_subset_attributes, lst=[]),
        friendly_name="Temperature"
    ),
    VisonicFloatSensorKey.ZONE_LUX: FloatSensorDefinition(
        key="zone_luminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=LIGHT_LUX,
        suggested_display_precision=1,
        source=EntityDataType.ZONE, # use the "zones"
        data_key=StateField.LUMINANCE,
        unique_extension="_luminance",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_float_normal,
        attributes_fn=partial(sensor_subset_attributes, lst=[]),
        friendly_name="Luminance"
    )
})

BINARY_SENSOR_DEFINITIONS: Mapping[
    VisonicBinarySensorKey,
    BinarySensorDefinition,
] = MappingProxyType( {
    VisonicBinarySensorKey.ZONE_TRIGGER: BinarySensorDefinition(
        key="zone_trigger",
        device_class=None, # When set to None then the class uses STYPE_TO_HA_SENSOR_MAP and the sensor type from ZoneSensorDetails
        source=EntityDataType.ZONE, # use the "zones"
        data_key=StateField.TRIGGERED,
        unique_extension="_trigger",
        translation_key=VISONIC_TRANSLATION_KEY,
        #value_fn=evaluate_binary_state_normal,
        value_fn=lambda x: x,
        attributes_fn=sensor_full_attributes,
        friendly_name="Zone"
    ),
    VisonicBinarySensorKey.ZONE_STATUS: BinarySensorDefinition(
        key="zone_status",
        device_class=None, # When set to None then the class uses STYPE_TO_HA_SENSOR_MAP and the sensor type from ZoneSensorDetails
        source=EntityDataType.ZONE, # use the "zones"
        data_key=StateField.STATUS,
        unique_extension="_status",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=sensor_full_attributes,
        friendly_name="Zone"
    ),
    VisonicBinarySensorKey.ZONE_CONTACT: BinarySensorDefinition(   # Much the same as previous but names have changed to live at the same time as Trigger
        key="zone_contact",
        device_class=BinarySensorDeviceClass.WINDOW,
        source=EntityDataType.ZONE, # use the "zones"
        data_key=StateField.STATUS,
        unique_extension="_contact",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=sensor_full_attributes,
        friendly_name="Contact"
    ),
    VisonicBinarySensorKey.ZONE_BATTERY: BinarySensorDefinition(
        key="zone_battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.BATTERY,
        source=EntityDataType.ZONE, # use the "zones"
        data_key=StateField.LOW_BATTERY,
        unique_extension="_battery",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=partial(sensor_subset_attributes, lst=[]),
        friendly_name="Battery"
    ),
    VisonicBinarySensorKey.DEVICE_BATTERY: BinarySensorDefinition(
        key="device_battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.BATTERY,
        source=EntityDataType.DEVICE, # use the "device"
        data_key=StateField.LOW_BATTERY,
        unique_extension="_battery",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=device_basic_attributes,
        friendly_name="Battery"
    ),
    VisonicBinarySensorKey.PANEL_BATTERY: BinarySensorDefinition(
        key="panel_battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.BATTERY,
        source=EntityDataType.PANEL,  # use the panelstate
        data_key=StateField.BATTERY_LEVEL,  # use this key to get the value from the panelstate
        unique_extension="_battery",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=empty_attributes,
        friendly_name="Battery"
    ),
    VisonicBinarySensorKey.ZONE_TAMPER: BinarySensorDefinition(
        key="zone_tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        source=EntityDataType.ZONE,
        data_key=StateField.ZONETAMPER,
        unique_extension="_tamper",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=partial(sensor_subset_attributes, lst=[]),
        friendly_name="Tamper"
    ),
    VisonicBinarySensorKey.PANEL_TAMPER: BinarySensorDefinition(
        key="panel_tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        source=EntityDataType.PANEL,
        data_key=StateField.TAMPER,
        unique_extension="_tamper",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=empty_attributes,
        friendly_name="Tamper"
    ),
    VisonicBinarySensorKey.ZONE_PROBLEM: BinarySensorDefinition(
        key="zone_problem",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.PROBLEM,
        source=EntityDataType.ZONE,
        data_key=StateField.PROBLEM,
        unique_extension="_problem",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=lambda value: evaluate_trouble(value),
        attributes_fn=partial(sensor_subset_attributes, lst=["zone_trouble"]),
        friendly_name="Trouble",
    ),
    VisonicBinarySensorKey.ZONE_MISSING: BinarySensorDefinition(
        key="zone_missing",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.PROBLEM,
        source=EntityDataType.ZONE,
        data_key=StateField.ISMISSING,
        unique_extension="_missing",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=partial(sensor_subset_attributes, lst=["zone_missing"]),
        friendly_name="Missing",
    ),
    VisonicBinarySensorKey.ZONE_ONEWAY: BinarySensorDefinition(
        key="zone_oneway",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.PROBLEM,
        source=EntityDataType.ZONE,
        data_key=StateField.ISONEWAY,
        unique_extension="_oneway",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=partial(sensor_subset_attributes, lst=["zone_oneway"]),
        friendly_name="One-Way",
    ),
    VisonicBinarySensorKey.ZONE_INACTIVE: BinarySensorDefinition(
        key="zone_inactive",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.PROBLEM,
        source=EntityDataType.ZONE,
        data_key=StateField.ISINACTIVE,
        unique_extension="_inactive",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=evaluate_binary_state_normal,
        attributes_fn=partial(sensor_subset_attributes, lst=["zone_inactive"]),
        friendly_name="Inactive",
    ),
    VisonicBinarySensorKey.PANEL_PROBLEM : BinarySensorDefinition(
        key="panel_problem",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=BinarySensorDeviceClass.PROBLEM,
        source=EntityDataType.PANEL,
        data_key=StateField.TROUBLE,
        unique_extension="_problem",
        translation_key=VISONIC_TRANSLATION_KEY,
        value_fn=lambda value: evaluate_trouble(value),
        attributes_fn=panel_trouble_attributes,
        friendly_name="Trouble",
    )
})

class SensorFeatures(IntFlag):
    """Sensor features when a sensor is created."""
    # These define what sensors are creted with the sensor
    NONE = 0
#    ZONE = 1
    BATTERY = 2
    TAMPER = 4
    TROUBLE = 8
    BYPASS = 16
#    IMAGE = 32
#    STATUS = 64

