"""Device type classes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .classes import BaseClass, title_case
from .const import TEXT_OPENED, TEXT_UNKNOWN, SensorGroup


@dataclass
class Device(BaseClass):
    """Base class definition of a device in the alarm system."""

    @property
    def bypass(self) -> bool | None:
        """Get if bypass enabled."""
        return self._get_nested_key("traits.bypass.enabled", None)

    @property
    def sensor_group(self) -> SensorGroup | None:
        """Get sensor_group."""
        return self._data.get("sensor_group", "")

    @property
    def device_number(self) -> int:
        """Get device number."""
        return self._data.get("device_number", 0)

    @property
    def device_type(self) -> str|None:
        """Get device type."""
        return self._data.get("device_type")

    @property
    def enrollment_id(self) -> str|None:
        """Get enrollment id."""
        return self._data.get("enrollment_id")

    @property
    def id(self) -> int:  # pylint: disable=invalid-name
        """Get device id."""
        return self._data.get("id", 0)

    @property
    @title_case
    def location(self) -> str:
        """Get device location."""
        return self._get_nested_key("traits.location.name")

    @property
    def name(self) -> str:
        """Get device name."""
        return self._data.get("name", TEXT_UNKNOWN)

    @property
    def partitions(self) -> list[str]:
        """Get device partitions."""
        return self._data.get("partitions", [])

    @property
    def preenroll(self) -> bool:
        """Get if device in preenroll."""
        return self._data.get("preenroll", False)

    @property
    def removable(self) -> bool:
        """Get if device removeable."""
        return self._data.get("removable", False)

    @property
    def renamable(self) -> bool:
        """Get if device renameable."""
        return self._data.get("renamable", False)

    @property
    def soak(self) -> bool:
        """Get if device part of soak test."""
        return self._get_nested_key("traits.soak.enabled", False)

    @property
    def subtype(self) -> str:
        """Get device subtype."""
        return self._data.get("subtype", TEXT_UNKNOWN)

    @property
    def warnings(self) -> dict[str, Any]:
        """Get device warnings."""
        return self._data.get("warnings", {})

    @property
    def zone_type_name(self) -> str:
        """Get device zone type."""
        return self._data.get("zone_type", TEXT_UNKNOWN)

    @property
    def low_battery(self) -> str:
        """Get device zone type."""
        return self._data.get("low_battery", TEXT_UNKNOWN)

    @property
    def trouble(self) -> str:
        """Get device zone type."""
        return self._data.get("trouble", "TEXT_UNKNOWN")

    @property
    def tamper(self) -> str:
        """Get device zone type."""
        return self._data.get("tamper", TEXT_UNKNOWN)

    @property
    def state(self) -> bool:
        """Returns the current state of the contact."""
        return self._data.get("state", TEXT_UNKNOWN)


@dataclass
class PanelDevice(Device):
    """Alarm Panel Device."""


@dataclass
class CameraDevice(Device):
    """Camera device class definition."""

    @property
    def vod(self) -> str:
        """Returns the current vod."""
        return self._get_nested_key("traits.vod", None)


@dataclass
class ContactDevice(Device):
    """Contact device class definition."""

@dataclass
class ShockDevice(Device):
    """Contact device class definition."""


@dataclass
class MotionDevice(Device):
    """Motion sensor device class definition."""

    @property
    def brightness(self) -> int:
        """Get brightness level."""
        return self._get_nested_key("traits.meteo_info.brightness.value")

    @property
    def brightness_last_updated(self) -> datetime:
        """Get brightness last updated."""
        return self._get_nested_key("traits.meteo_info.brightness.date")

    @property
    def temperature(self) -> float:
        """Get temperature."""
        return self._get_nested_key("traits.meteo_info.temperature.value")

    @property
    def temperature_last_updated(self) -> datetime:
        """Get temperature last updated."""
        return self._get_nested_key("traits.meteo_info.temperature.date")


@dataclass
class GenericDevice(Device):
    """Generic device class definition."""


@dataclass
class GSMDevice(Device):
    """GSM device class definition."""

    @property
    def signal_level(self) -> str | None:
        """Get signal level."""
        return self._get_nested_key("traits.signal_level.level")


@dataclass
class KeyFobDevice(Device):
    """KeyFob device class definition."""

    @property
    def owner_id(self) -> int:
        """Get owner id."""
        return self._get_nested_key("traits.owner.id", 0)

    @property
    def owner_name(self) -> str:
        """Get owner name."""
        return self._get_nested_key("traits.owner.name", TEXT_UNKNOWN)

    @property
    def state(self) -> bool:
        """Returns the current state of the keyfob."""
        if self.warnings:
            return TEXT_OPENED in str(self.warnings)
        return False


@dataclass
class TagDevice(Device):
    """Tag device class definition."""


@dataclass
class PGMDevice(Device):
    """PGM device class definition."""

    @property
    def parent_id(self) -> int:
        """Get parent id."""
        return self._get_nested_key("traits.parent.id", 0)

    @property
    def parent_port(self) -> int:
        """Get parent port."""
        return self._get_nested_key("traits.parent.port", 0)


@dataclass
class SmokeDevice(Device):
    """Smoke device class definition."""
