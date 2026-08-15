"""Sensor."""
from __future__ import annotations  # noqa: TID251

from datetime import datetime
import logging
from typing import Any

from .py_abstract_classes import AlSensorDevice
from .py_enum import AlSensorCondition
from .py_utils import get_local_time

log = logging.getLogger(__name__)

class AlSensorDeviceHelper(AlSensorDevice):
    """Sensor Device Helper Class."""

    def __init__(self, id: int) -> None:
        """Initialize the AlSensorDeviceHelper."""
        super().__init__(id)
        #self._motion_delay_time = None
        self._temperature: float | None = None
        self._luminance: float | None = None
        self._tamper: bool = False
        self.status_log: datetime | None = None

    def __str__(self):
        """Convert the AlSensorDeviceHelper to a string."""
        pt = ""
        for i in self._partition:
            pt = pt + str(i) + " "

# fmt: off

        strn = ""
        strn = strn + ("id=None" if self.id is None else f"id={self.id:<2}")
        strn = strn + (" partition=None   "    if self.partition is None      else f" partition={pt:<7}")
        strn = strn + (" bypass=- "            if self.bypass is None         else f" bypass={self.bypass:<2}")
        strn = strn + (" batt=- "              if self.low_battery is None    else f" batt={self.low_battery:<2}")
        strn = strn + (" stat=- "              if self.is_open is None        else f" stat={self.is_open:<2}")
        strn = strn + (" tamp=- "              if self.tamper is None         else f" tamp={self.tamper:<2}")
        strn = strn + (" enrol=- "             if self.enrolled is None       else f" enrol={self.enrolled:<2}")
        strn = strn + (" trig=- "              if self.triggered is None      else f" trig={self.triggered:<2}")
        strn = strn + (" ztamp=- "             if self.zone_tamper is None    else f" ztamp={self.zone_tamper:<2}")
        strn = strn + (" ztrip=- "             if self.zone_trip is None      else f" ztrip={self.zone_trip:<2}")
        strn = strn + (" sid=None"             if self.raw_sensor_id is None  else f" sid={self.raw_sensor_id:<3}")
        strn = strn + (" ztype=None"           if self.zone_type is None      else f" ztype={self.zone_type:<2}")
        strn = strn + (" loc=None          "   if self.zone_name is None      else f" loc={self.zone_name[:14]:<14}")
        strn = strn + (" zname=None          " if self.zone_type_name is None else f" zname={self.zone_type_name[:10]:<10}")
        strn = strn + (" zchime=None         " if self.zone_chime is None     else f" zchime={self.zone_chime:<13}")
        strn = strn + (""                      if self.temperature is None    else f" temperature={self.temperature:<3}")
        return strn + (""                      if self.lux is None            else f" luminance={self.lux:<3}")

# fmt: on

    def as_dict(self) -> dict[str, Any]:
        """Return a dict of all params."""
        return {
             "id": self._sensor_id,
             "problem": self.problem,
             #"sensortype": self.sensor_type,
             "sensor_type_id": self.raw_sensor_id,
             "partition": self.partition,
             "location": self.zone_location,
             "zonetype": self.zone_type_name,
             #"model": self.model,
             "chime": self.zone_chime,
             "bypass": self.bypass,
             "low_battery": self.low_battery,
             "status": self.is_open,
             "tamper": self.tamper,
             "enrolled": self.enrolled,
             "triggered": self.triggered,
             "zonetamper": self.zone_tamper,
             #"zone_trip": self.zone_trip,
             "temperature": self.temperature,
             "luminance": self.lux,
             "ismissing": self.is_missing,
             "isoneway": self.is_one_way,
             "isinactive": self.is_inactive,
             #"offtime": self.motion_delay_time,
             "has_image": self.has_jpg,
             "image_is_audio": self.jpg_is_audio,
             "image_data": self.jpg_data,
             "image_time": self.jpg_timestamp,
             "time": self.last_trigger_time     # When triggered
        }

    def __eq__(self, other: AlSensorDeviceHelper):
        """Test equality of two AlSensorDeviceHelper objects."""
        if not isinstance(other, AlSensorDeviceHelper):
            return False
        return (
            self._sensor_id == other._sensor_id
            and self.partition == other.partition
            #and self.sensor_type == other.sensor_type
            and self.raw_sensor_id == other.raw_sensor_id
            #and self.model == other.model
            and self.zone_type == other.zone_type
            and self.zone_name == other.zone_name
            and self.zone_panel_name == other.zone_panel_name
            and self.zone_chime == other.zone_chime
            and self.bypass == other.bypass
            and self.low_battery == other.low_battery
            and self.is_open == other.is_open
            and self.tamper == other.tamper
            and self.is_missing == other.is_missing
            and self.is_one_way == other.is_one_way
            and self.is_inactive == other.is_inactive
            and self.zone_type_name == other.zone_type_name
            and self.zone_tamper == other.zone_tamper
            and self.zone_trip == other.zone_trip                       # Needs a setter getter
            and self.enrolled == other.enrolled
            and self.triggered == other.triggered
            and self.has_jpg == other.has_jpg
            #and self.last_trigger_time == other.triggertime
            #and self.motion_delay_time == other.motion_delay_time
        )


    def __ne__(self, other):
        """Not equal operator for AlSensorDeviceHelper."""
        return not self.__eq__(other)

    def do_status(self, stat):
        """Do status update."""
        if stat is not None and self.is_open != stat:
            # The current setting is different
            log.debug("[UpdateContactSensor]   Sensor %s   status from %s to %s", self._sensor_id, self.is_open, stat)
            self.is_open = stat
            self.notify(AlSensorCondition.STATE)

    def do_trigger(self, notused):
        """Do trigger update."""
        # If trigger is set then the caller is confident that it is a motion or camera sensor
        self.triggered = (self.triggered + 1) % 100
        self.last_trigger_time = get_local_time()
        log.debug("[UpdateContactSensor]   Sensor %s   triggered = %s    at time=%s", self._sensor_id, self.triggered, self.last_trigger_time )
        self.notify(AlSensorCondition.TRIGGER)

    def do_bypass(self, val: bool) -> bool:
        """Do bypass update."""
        if val is not None and self._bypass != val:
            self._bypass = val
            self.notify(AlSensorCondition.BYPASS if self._bypass else AlSensorCondition.ARMED)
            return True # The value has changed
        return False # The value has not changed

    @property
    def bypass(self) -> bool:
        """Get bypass sensor."""
        return self._bypass

    def do_missing(self, val: bool) -> bool:
        """Do missing update."""
        if val is not None and self._is_missing != val:
            self._is_missing = val
            self.notify(AlSensorCondition.STATE)
            return True # The value has changed
        return False # The value has not changed

    @property
    def is_missing(self) -> bool:
        """Get missing sensor."""
        return self._is_missing

    def do_inactive(self, val: bool) -> bool:
        """Do inactive update."""
        if val is not None and self._is_inactive != val:
            self._is_inactive = val
            self.notify(AlSensorCondition.STATE)
            return True # The value has changed
        return False # The value has not changed

    @property
    def is_inactive(self) -> bool:
        """Get inactive sensor."""
        return self._is_inactive

    def do_oneway(self, val: bool) -> bool:
        """Do one way update."""
        if val is not None and self._is_one_way != val:
            self._is_one_way = val
            self.notify(AlSensorCondition.STATE)
            return True # The value has changed
        return False # The value has not changed

    @property
    def is_one_way(self) -> bool:
        """Get one way comms."""
        return self._is_one_way

    def do_ztrip(self, val: bool) -> bool:
        """Do zone_trip update."""
        if val is not None and self._zone_trip != val:
            self._zone_trip = val
            self.notify(AlSensorCondition.STATE)
            return True # The value has changed
        return False # The value has not changed

    @property
    def zone_trip(self) -> bool:
        """Get zone tripped."""
        return self._zone_trip

    def do_ztamper(self, val: bool) -> bool:
        """Do tamper update."""
        if val is not None and self._zone_tamper != val:
            self._zone_tamper = val
            self.notify(AlSensorCondition.TAMPER if self._zone_tamper else AlSensorCondition.RESTORE)
            return True # The value has changed
        return False # The value has not changed

    @property
    def zone_tamper(self) -> bool:
        """Get tamper."""
        return self._zone_tamper

    def do_battery(self, val: bool) -> bool:
        """Do battery update."""
        if val is not None and self._low_battery != val:
            self._low_battery = val
            self.notify(AlSensorCondition.BATTERY)
            return True # The value has changed
        return False # The value has not changed

    @property
    def low_battery(self) -> bool:
        """Get low battery."""
        return self._low_battery

    def do_tamper(self, val: bool) -> bool:
        """Do tamper update."""
        if val is not None and self._tamper != val:
            self._tamper = val
            self.notify(AlSensorCondition.TAMPER if self._tamper else AlSensorCondition.RESTORE)
            return True # The value has changed
        return False # The value has not changed

    @property
    def tamper(self) -> bool:
        """Get tamper."""
        return self._tamper

    @property
    def lux(self) -> float | None:
        """Get the lux value."""
        return self._luminance

    @lux.setter
    def lux(self, value: float | None) -> None:
        if value is not None and self._luminance != value:
            log.debug("[updateLux]   Lux old %s   new %s", self._luminance, value )
            self._luminance = value
            self.notify(AlSensorCondition.LUX)

    @property
    def temperature(self) -> float | None:
        """Get the temperature value."""
        return self._temperature

    @temperature.setter
    def temperature(self, value: float | None) -> None:
        """Update the temperature value."""
        if value is not None and self._temperature != value:
            # log.debug(f"[updateTemperature]   Temperature old {self._temperature}   new {value}")
            self._temperature = value
            self.notify(AlSensorCondition.TEMPERATURE)
