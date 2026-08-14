"""Pyvisonic type definitions and classes."""

from dataclasses import dataclass, field
from enum import IntEnum
import logging
from typing import Any

from .py_const import MAX_PARTITIONS

log = logging.getLogger(__name__)

@dataclass(slots=True)
class AlPanelEventData:
    """Data class representing a panel event."""
    name: int = 0
    action: int = 0
    time: str = field(default="", compare=False)  # 👈 excluded from __eq__
    partition: int = -1

    def __str__(self):
        """Data class representing a panel event."""
        return f"{self.time} {self.partition} {self.name} {self.action}"

    def set_partition(self, p: int):
        """Data class representing a panel event."""
        if 0 <= p < MAX_PARTITIONS:
            self.partition = p

    def as_dict(self) -> dict[str, int | str]:
        """Data class representing a panel event."""
        data : dict[str, Any] = {
            "name": self.name,
            "event": self.action,
            "time": self.time,
        }
        if 0 <= self.partition < MAX_PARTITIONS:
            data["partition"] = self.partition
        return data

class AlCommandedModeType(IntEnum):
    """Data class representing a commanded mode type."""
    POWERLINK = 1
    FORCE_STANDARD_BY_USER = 2
    FORCE_STANDARD_BY_PANEL = 3
