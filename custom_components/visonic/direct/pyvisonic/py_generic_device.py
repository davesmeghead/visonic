"""Generic Device."""
from __future__ import annotations  # noqa: TID251

import logging
from typing import Any

from .py_abstract_classes import AlGenericDevice, GenericDeviceType

log = logging.getLogger(__name__)


class AlGenericDeviceHelper(AlGenericDevice):
    """Device Helper Class."""

    def __str__(self):
        """Convert the AlGenericDeviceHelper to a string."""
        strn = ""
        strn = strn + ("type=None" if self._device_type is None else f"type={self._device_type.name}")
        strn = strn + ("id=None" if self._device_id is None else f"id={self._device_id:<2}")
        strn = strn + (" Model=None          " if self.model is None else f" Type={self.model:<15}")
        strn = strn + (" Name=None           " if self.device_name is None else f" Name={self.device_name:<15}")
        strn = strn + (" Loc=None          " if self.location is None else f" Loc={self.location:<14}")
        strn = strn + (f" enabled={self.enabled:<2}")
        return strn + (f" state={self._state:<8}")

    def as_dict(self) -> dict[str, Any]:
        """Return switch data as a dict."""
        return {
             "id": self._device_id,
             "device_type": self._device_type.name,
             "low_battery": self.low_battery,
             "enabled": self.enabled,
             "model": self.model,
             "name": self.device_name,
             "location": self.location
        }

    def __eq__(self, other: AlGenericDeviceHelper):
        """Test equality of two AlGenericDeviceHelper objects, ignoring state."""
        if not isinstance(other, AlGenericDeviceHelper):
            return False
        return (
            self._device_id == other._device_id
            and self._device_type == other._device_type
            and self.enabled == other.enabled
            and self.model == other.model
            and self.device_name == other.device_name
            and self.location == other.location
        )

    def __ne__(self, other):
        """Test inequality of two AlGenericDeviceHelper objects, ignoring state."""
        return not self.__eq__(other)

    @classmethod
    def make_key(cls, t: GenericDeviceType, i: int ) -> str:
        """Class method to make the unique key for lists and dicts."""
        return f"{t.name.lower()}_{i}"


#class KeyFobDevice(AlGenericDeviceHelper):
#    """Keyfobs."""
#    def __init__(self, id: int, model: str = "", device_name: str = "", location: str = "", enabled: bool = False) -> None:
#        """Initialise the sensor parameters."""
#        super().__init__(id, model, device_name, location, enabled)

#class KeyPadOnewayDevice(AlGenericDeviceHelper):
#    """Keypad Oneway."""
#    def __init__(self, id: int, model: str = "", device_name: str = "", location: str = "", enabled: bool = False) -> None:
#        """Initialise the sensor parameters."""
#        super().__init__(id+40, model, device_name, location, enabled)

#class KeyPadTwowayDevice(AlGenericDeviceHelper):
#    """Keypad Twoway."""
#    def __init__(self, id: int, model: str = "", device_name: str = "", location: str = "", enabled: bool = False) -> None:
#        """Initialise the sensor parameters."""
#        super().__init__(id+50, model, device_name, location, enabled)
