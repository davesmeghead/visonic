"""Panel Event Coordinator."""

# The panel creates events but in many cases the event is the same as last time, and sometimes within a second of each other.
# This class TRIES TO filter out duplicates but cannot be too aggressive as sometimes duplicates are relevant and needed.

from collections.abc import Callable

# visonic/client.py
import copy
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import CALLBACK_TYPE
from homeassistant.helpers.event import async_call_later

from ..const import PE_EVENT, PE_NAME, PE_PARTITION, PE_TIME  # noqa: TID252
from ..log_events import logEvents  # noqa: TID252
from ..visonic_types import PanelCondition  # noqa: TID252
from .language_decoder import LanguageDecoder


class PanelEventCoordinator:
    """Coordinate panel events."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        callbackSender: Callable[..., None],
        language_decoder: LanguageDecoder,
        logger: logEvents,
        ispm: bool = False,
    ) -> None:
        """Coordinate panel events."""
        self.logger = logger
        self.callbackSender = callbackSender or None

        #self.logger.logstate_debug("[EC] Starting")
        self.hass = hass
        self.entry = entry
        self.languageDecoder: LanguageDecoder = language_decoder
        self.is_power_master: bool = ispm
        self._init_vars()

    def _init_vars(self):
        """Coordinate panel events."""
        self.EventTime = 0
        self.EventName = 0
        self.EventAction = -100
        self.EventPartition = None
        self._my_event_timer_task: CALLBACK_TYPE | None = None
        self._timer_already_sent = True
        self._save_sent_data = None
        self._save_time = None

    async def close(self):
        """Coordinate panel events."""
        if self._my_event_timer_task is not None:
            self._my_event_timer_task()
        self._init_vars()

    def _sendData(self):
        """Coordinate panel events."""
        if self.EventAction >= 0:
            _save = [self.EventName, self.EventAction, self.EventPartition or "nopartition"]
            if self._save_sent_data is not None and self._save_sent_data == _save:
                if self._save_time > self.EventTime:
                    self.logger.logstate_debug(f"We seem to have a new event with an old timestamp {self.EventName=} {self.EventAction=} as data {self._convert()}")
                    return
                time_diff = self.EventTime - self._save_time
                if time_diff < timedelta(seconds = 5):
                    return
            self._save_sent_data = copy.deepcopy(_save)
            self._save_time = self.EventTime
            d = self._convert()
            #self.logger.logstate_debug(
            #    f"[EC] sending panel update {self.EventName=} {self.EventAction=} as data {d}"
            #)
            self.callbackSender(PanelCondition.PANEL_UPDATE, d)
        else:
            self.logger.logstate_info("[EC] _sendData wont send blank data")

    def _convert(self) -> dict[str, Any]:
        """Coordinate panel events."""
        d: dict[str, Any] = {}
        # Set the name
        d[PE_NAME] = "Unknown"
        if self.is_power_master:
            d[PE_NAME] = (
                self.languageDecoder.user_log_power_master[int(self.EventName & 0x7F)]
                or "Unknown"
            )
        else:
            d[PE_NAME] = (
                self.languageDecoder.user_log_power_max[int(self.EventName & 0x7F)]
                or "Unknown"
            )
        # Set the event
        d[PE_EVENT] = self.languageDecoder.get_event_entry(self.EventAction)

        # Set the time
        d[PE_TIME] = self.EventTime
        if self.EventPartition is not None:
            d[PE_PARTITION] = self.EventPartition
        return d

    async def _event_timer(self, now: datetime | None = None):
        """Coordinate panel events."""
        self._sendData()
        self._timer_already_sent = True

    def _send_and_replace(self, data: dict[str, Any]):
        """Coordinate panel events."""
        if self._my_event_timer_task is not None:
            self._my_event_timer_task()
            self.logger.logstate_debug("[EC] Cancelled _event_timer_task")
        # send existing data
        if not self._timer_already_sent:
            self._sendData()
        # save new data
        self.EventName = data[PE_NAME]
        self.EventAction = data[PE_EVENT]
        self.EventTime = data[PE_TIME]
        self.EventPartition = data.get(PE_PARTITION)  # Returns None if not present
        self._timer_already_sent = False
        # Send the data in 1 seconds time, if no other events come in
        self._my_event_timer_task = async_call_later(self.hass, 1, self._event_timer)

    def addEvent(self, pm: bool, data: dict[str, Any] | None) -> bool:
        """Coordinate panel events."""
        self.is_power_master = pm
        if data is not None:
           # self.logger.logstate_debug(f"[EC] addEvent {data}")
            if self.EventAction != data[PE_EVENT]:
                # If the action is not the same
                self._send_and_replace(data)
                return True
            # If the action is the same
            if (
                self.EventName == data[PE_NAME]
            ):  # exactly the same event as last time then do not send it
                # Name is exactly the same as what we already have
                #self.logger.logstate_debug(f"[EC] Panel event data {data} is the same as last time so not sending event")
                return True
            if self.EventName != 0 and data[PE_NAME] == 0:
                # Existing Name is better than new one
                #self.logger.logstate_debug(
                #    f"[EC] Panel event data {data} is the same Event but I already have a better name"
                #)
                return False
            if self.EventName == 0 and data[PE_NAME] != 0:
                # The existing name is 0 (i.e. system) and the new name is better so replace it
                #self.logger.logstate_debug(
                #    f"[EC] Replacing 'system' with {data[PE_NAME]} but keeping original time {self.EventTime}"
                #)
                self.EventName = data[PE_NAME]
                # self.EventTime = data[PE_TIME]
            # Here when the existing name and the new name are different and both non-zero
            #   Send the previous and replace with the new
            self._send_and_replace(data)
            return True
        return False
