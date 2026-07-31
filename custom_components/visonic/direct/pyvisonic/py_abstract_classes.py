"""Abstract base classes."""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable
from datetime import datetime
from enum import Enum, auto
from typing import Any

from .py_enum import (
    AlAlarmType,
    AlCommandStatus,
    AlPanelCommand,
    AlPanelMode,
    AlPanelStatus,
    AlSensorCondition,
    AlSwitchCommand,
)


class CallbackHandler:
    """Common callback handler to notify of change."""

    # ---- callback system ----
    def __init__(self) -> None:
        """Initialise the sensor parameters."""
        self._callbacks: list[Callable[..., None]] = []

    def add_callback(
        self,
        callback: Callable[..., None],
    ) -> None:
        """Add a callback."""
        self._callbacks.append(callback)

    def clear_callbacks(self) -> None:
        """Remove all callbacks."""
        self._callbacks.clear()

class AlSensorDevice(CallbackHandler, ABC):
    """Abstract base class for sensor devices."""

    def __init__(self, sensor_id: int) -> None:
        """Initialise the sensor parameters."""
        # The variables that have _ are protected and have "do" functions and getters
        super().__init__()
        self._sensor_id: int = sensor_id     # immutable internal identity (creation-time)
        self.device_id: int = -1             # external / panel / runtime ID
        self.raw_sensor_id: int = 0          # hardware / protocol ID
        self._partition: set[int] = set()
        self._zone_tamper: bool = False
        self._bypass: bool = False
        self._low_battery: bool = False
        self._is_missing: bool | None = None
        self._is_inactive: bool | None = None
        self._is_one_way: bool | None = None
        #self._model: str | None = None
        self.jpg_timestamp: datetime | None = None
        self.jpg_data: bytearray | None = None
        self.jpg_is_audio: bool = False           # the buffer is the capture's audio clip, not a frame
        self.last_trigger_time: datetime | None = None
        self.zone_chime: str = ""
        self._zone_trip: bool = False
        self.zone_type_name: str = ""
        self.has_jpg: bool = False
        self.triggered: int = 0
        self.is_open: bool | None = None
        self.enrolled: bool = False
        self.device_tamper: bool = False
        self.problem: str = "none"
        self.zone_name: str = ""
        self.zone_panel_name: str = ""
        self.zone_type: int = 0

    # ---- Required API ----

    @abstractmethod
    def __str__(self) -> str:
        """Abstract base class for sensor devices."""

    @abstractmethod
    def as_dict(self) -> dict[str, Any]:
        """Return a dictionary of the parameters in the class."""

    @property
    @abstractmethod
    def lux(self) -> float | None:
        """Get the lux value."""

    @property
    @abstractmethod
    def temperature(self) -> float | None:
        """Get the temperature value."""

#    #    This is only applicable to PowerMaster Panels. It is the motion off time per sensor.
#    @property
#    @abstractmethod
#    def motion_delay_time(self) -> int:
#        """Get the motion delay time."""

    # ---- Convenience properties ----

    @property
    def id(self) -> int:
        """Getter for the id."""
        return self._sensor_id

    #@property
    #def model(self) -> str:
    #    """Model."""
    #    return self._model or "Unknown"

    #@model.setter
    #def model(self, model: str) -> None:
    #    """Set model."""
    #    self._model = model

    @property
    def partition(self) -> set[int]:
        """Return a copy to protect internal state."""
        return self._partition.copy()

    def add_to_partition(self, value: int) -> None:
        """Add to partition."""
        if value not in self._partition:
            self._partition.add(value)

    @property
    def zone_location(self) -> tuple[str, str]:
        """Return a tuple with both zone names."""
        return self.zone_name, self.zone_panel_name

    def notify(self, condition: AlSensorCondition) -> None:
        """Notify all callback handlers of a change."""
        for cb in list(self._callbacks):
            cb(self, condition)

class AlSwitchDevice(CallbackHandler, ABC):
    """Abstract base class for sensor devices."""

    def __init__(self, id: int, switch_type: str = "", location: str = "", enabled: bool = False) -> None:
        """Initialise the sensor parameters."""
        # The variables that have _ are protected and have "do" functions and getters
        super().__init__()
        self._device_id: int = id            # immutable internal identity (creation-time)
        self._state = False
        self.switch_type = switch_type
        self.location = location
        self.enabled = enabled

    # ---- Required API ----

    @abstractmethod
    def __str__(self) -> str:
        """Abstract base class for sensor devices."""

    @abstractmethod
    def as_dict(self) -> dict[str, Any]:
        """Return a dictionary of the parameters in the class."""

    # ---- Convenience properties ----

    @property
    def id(self) -> int:
        """Getter for the id."""
        return self._device_id

    @property
    def state(self) -> bool:
        """Is the switch on."""
        return self._state

    @state.setter
    def state(self, value: bool) -> None:
        if self._state != value:
            self._state = value
            self.notify()

    def notify(self) -> None:
        """Notify all callback handlers of a change."""
        for cb in list(self._callbacks):
            cb(self)

class GenericDeviceType(Enum):
    """Generic device types."""
    KEYFOB = auto()
    KEYPAD1 = auto()
    KEYPAD2 = auto()
    SIREN = auto()

class AlGenericDevice(CallbackHandler, ABC):
    """Abstract base class for sensor devices."""

    def __init__(self, t: GenericDeviceType, id: int, model: str = "", device_name: str = "", location: str = "", enabled: bool = False) -> None:
        """Initialise the sensor parameters."""
        super().__init__()
        self._device_type: GenericDeviceType = t
        self._device_id: int = id
        self._state = True
        self.low_battery = False
        self.model = model
        self.device_name = device_name
        self.location = location
        self.enabled = enabled

    # ---- Required API ----

    @abstractmethod
    def __str__(self) -> str:
        """Abstract base class for sensor devices."""

    @abstractmethod
    def as_dict(self) -> dict[str, Any]:
        """Return a dictionary of the parameters in the class."""

    # ---- Convenience properties ----

    @property
    def device(self) -> GenericDeviceType:
        """Getter for the GenericDeviceType."""
        return self._device_type

    @property
    def id(self) -> int:
        """Getter for the id."""
        return self._device_id

    @property
    def state(self) -> bool:
        """Is the switch on."""
        return self._state

    @state.setter
    def state(self, value: bool) -> None:
        if self._state != value:
            self._state = value
            self.notify()

    def notify(self) -> None:
        """Notify all callback handlers of a change."""
        for cb in list(self._callbacks):
            cb(self)

class AlPanelDataStream(ABC):
    """Abstract base class for panel data stream handling (receiving data)."""

    @abstractmethod
    def set_transport(self, transport : asyncio.Transport) -> None:
        """Abstract base class for panel data stream handling."""

    @abstractmethod
    def data_received(self, data : bytearray) -> None:
        """Abstract base class for panel data stream handling."""

# the underlying class implements these so you can call them
class AlPanelInterface(AlPanelDataStream):
    """Abstract base class for panel interface operations."""

    @abstractmethod
    def shutdown(self) -> None:
        """Terminate the connection to the panel."""

    @abstractmethod
    def start(self):
        """Start the internal processing e.g. despatcher/sequencer."""

    @abstractmethod
    def pause(self):
        """Pause the internal processing e.g. despatcher/sequencer."""

    @abstractmethod
    def resume(self):
        """Resume the internal processing e.g. despatcher/sequencer."""

    @abstractmethod
    def reset_full(self):
        """Reset all non-permanent variables."""

    @abstractmethod
    def reset_connection(self):
        """Reset variables associated with the current connection only."""

    @abstractmethod
    def is_siren_active(self, partition : int) -> tuple[bool, int, AlAlarmType]:
        """Is the siren active."""

    @abstractmethod
    def get_partition_status(self, partition : int) -> AlPanelStatus:
        """Get the panel state i.e. Disarmed, Arming Home etc."""

    @abstractmethod
    def get_panel_mode(self) -> AlPanelMode:
        """Get the panel Mode e.g. Standard, Powerlink etc."""

    @abstractmethod
    def is_power_master(self) -> bool:
        """Get the panel type, PowerMaster or not."""

    @abstractmethod
    def get_partitions_in_use(self) -> set[int] | None:  # returns None if not yet known
        """Get the partitions in use."""

    @abstractmethod
    def get_panel_model(self) -> str:
        """Get the panel model."""

    @abstractmethod
    def is_panel_ready(self, _partition : int) -> bool:
        """Get the panel ready state."""

    @abstractmethod
    async def set_panel_baud(self, baudrate : int)  -> AlCommandStatus:
        """Set the panel baud rate."""

    @abstractmethod
    def get_partition_status_dict(self, partition : int) -> dict[str, Any]:
        """Get a dictionary representing the partition status."""

    # A dictionary that is used to add to the attribute list of the Alarm Control Panel
    #     If this is overridden then please include the items in the dictionary defined here by using super()
    @abstractmethod
    def get_panel_status_dict(self, include_extended_status : bool | None = None) -> dict[str, Any]:
        """Get a dictionary representing the panel status."""

    # Arm / Disarm the Panel
    # state is the command to set the panel state i.e. disarm, arm_away etc
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this may work depending on the panel type for arming, but not for disarming)
    @abstractmethod
    def panel_command(self, state : AlPanelCommand, code : None | str = "", partitions : None | set[int] = None) -> AlCommandStatus:
        """Send a request to the panel to Arm/Disarm."""

    # device in range 0 to 15 (inclusive), 0=PGM, 1 to 15 are switch devices
    # state is the switch state to set the switch
    @abstractmethod
    def send_switch(self, device : int, state : AlSwitchCommand) -> AlCommandStatus:
        """Set the state of a switch."""

    @abstractmethod
    def get_sensor_image(self, device : int, count : int) -> AlCommandStatus:
        """Get jpg image."""

    @abstractmethod
    def get_sensor_bypass_state(self) -> None:
        """Request a sensor bypass state update."""

    @abstractmethod
    def sensors_to_string_list(self) -> list[str]:
        """Dump sensors to a string list."""

    @abstractmethod
    def switches_to_string_list(self) -> list[str]:
        """Dump switches to a string list."""

    # @abstractmethod
    # def dumpStateToStringList(self) -> list:
    #    return []

    # Set the Sensor Bypass to Arm/Bypass individual sensors
    # sensor in range 1 to 31 for PowerMax and 1 to 63 for PowerMaster (inclusive) depending on alarm
    # bypassValue is False to Arm the Sensor and True to Bypass the sensor
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def bypass_command(self, sensor : int | set[int], bypassValue : bool, code : None | str = "") -> AlCommandStatus:
        """Set or Clear Sensor Bypass."""

    # Get the panels event log
    # Set code to:
    #    None when we are in Powerlink or Standard Plus and to use the code code from EPROM
    #    "1234" a 4 digit code for any panel mode to use that code
    #    anything else to use code "0000" (this is unlikely to work on any panel)
    @abstractmethod
    def get_event_log(self, code : None | str = "") -> AlCommandStatus:
        """Get Panel Event Log."""

    # Set the on_panel_change callback handlers
    @abstractmethod
    def on_panel_change(self, fn : Callable[..., None]) -> None:             # on_panel_change ( event_id : AlCondition )
        """Onpanelchange callback."""

    # Set the on_problem callback handlers
    @abstractmethod
    def on_problem(self, fn : Callable[..., None]) -> None:             # on_problem ( reason: str, ex : exception or None )
        """On problem callback."""

    # Set the on_new_sensor callback handlers
    @abstractmethod
    def on_new_sensor(self, fn : Callable[..., None]) -> None:             # on_new_sensor ( device : AlSensorDevice )
        """On new sensor callback."""

    # Set the on_new_switch callback handlers
    @abstractmethod
    def on_new_switch(self, fn : Callable[..., None]) -> None:             # on_new_switch ( sensor : AlSwitchDevice )
        """On new switch callback."""

    # Set the on_panel_event_log callback handlers
    @abstractmethod
    def on_panel_event_log(self, fn : Callable[..., None]) -> None:
        """On panel event log callback."""

    @abstractmethod
    def set_log_events(self, logevents : list[str]) -> None:
        """Set the log event list."""
