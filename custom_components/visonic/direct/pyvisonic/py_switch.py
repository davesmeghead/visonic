"""Switch."""
from __future__ import annotations

import logging
from typing import Any

from .py_abstract_classes import AlSwitchDevice

log = logging.getLogger(__name__)


class AlSwitchDeviceHelper(AlSwitchDevice):
    """Switch Device Helper Class."""

    def __str__(self):
        """Convert the AlSwitchDeviceHelper to a string."""
        strn = ""
        strn = strn + ("id=None" if self.id is None else f"id={self.id:<2}")
        strn = strn + (" Type=None           " if self.switch_type is None else f" Type={self.switch_type:<15}")
        strn = strn + (" Loc=None          " if self.location is None else f" Loc={self.location:<14}")
        strn = strn + (f" enabled={self.enabled:<2}")
        return strn + (f" state={self.state:<8}")

    def as_dict(self) -> dict[str, Any]:
        """Return switch data as a dict."""
        return {
             "id": self.id,
             "status": self.state,
             "enabled": self.enabled,
             "model": self.switch_type,
             "location": self.location
        }

    def __eq__(self, other: AlSwitchDeviceHelper):
        """Test equality of two AlSwitchDeviceHelper objects."""
        if not isinstance(other, AlSwitchDeviceHelper):
            return False
        return (
            self.id == other.id
            and self.enabled == other.enabled
            and self.switch_type == other.switch_type
            and self.location == other.location
        )

    def __ne__(self, other):
        """Test inequality of two AlSwitchDeviceHelper objects."""
        return not self.__eq__(other)
