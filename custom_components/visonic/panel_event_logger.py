"""Helper classes for the client - Panel Event Log.

This class captures and saves the panel event log as it is transferred from the panel.
"""
import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import datetime

from .const import (
    CONF_LOG_CSV_FN,
    CONF_LOG_CSV_TITLE,
    CONF_LOG_DONE,
    CONF_LOG_EVENT,
    CONF_LOG_MAX_ENTRIES,
    CONF_LOG_REVERSE,
    CONF_LOG_XML_FN,
    TEXT_XML_LOG_FILE_TEMPLATE,
)
from .log_events import logEvents
from .utils import to_bool
from .visonic_types import AvailableNotifications, PanelCondition

###################################################################################
##############  Create and manage the output for csv and xml Panel Event Log ######
###################################################################################

# If a block of BLOCK_OF_EVENTS entries have not been received in BLOCK_OF_TIME seconds then timeout and save the files, quit the event sequence
BLOCK_OF_EVENTS = 5
BLOCK_OF_TIME = 10.0

class PanelEventLogger:
    """Panel Event Logger."""

    # Panel log entries, save to file
    #   This class saves the panel log file history to a csv and xml file

    def __init__(
        self,
        hass: HomeAssistant,
        panelident: int,
        entry: ConfigEntry,
        logger: logEvents,
        create_ha_fire_event: Callable[..., None] | None,
    ) -> None:
        """Initialize the Event Logger."""
        self.hass = hass
        self.entry = entry
        self.logger = logger
        self.panel_ident = panelident
        # Language translations for the event zone/location
        # For firing the HA event on each event and then on completion
        self.create_ha_fire_event = create_ha_fire_event
        # variables for creating the event log for csv and xml
        self.collating_data = False
        self.csvdata = []
        self.xmldata = []
        self.save_task = None
        self.timer_task = None
        # Create the jinja environment
        file_loader = FileSystemLoader(
            [
                self.hass.config.path() + "/templates",
                self.hass.config.path() + "/xml",
                self.hass.config.path() + "/www",
                self.hass.config.path(),
            ],
            followlinks=True,
        )
        self.jinja_env = Environment(loader=file_loader)

    async def _count_down_time_out(self, current: int, total: int, reverse: bool):
        try:
            # If a block of BLOCK_OF_EVENTS entries have not been received in BLOCK_OF_TIME seconds then timeout and save the files, quit the event sequence
            await asyncio.sleep(BLOCK_OF_TIME)
        except asyncio.CancelledError:
            return
        # Assume that getting event log messages from the panel have stopped so save stuff
        #  We get to here when the panel stops sending event log data before it gets to the last event
        self.logger.logstate_warning(
            "Saving event log, the event log data has stopped from the panel, the countdown timer has been triggered to save the data"
        )
        if self.save_task is None:
            self.save_task = self.entry.async_create_task(
                self.hass,
                self._savePanelEventLogFiles(current, total, reverse),
            )

    def _stop_countdown_timer(self):
        if self.timer_task is not None:
            # Cancel the timer task if it exists
            try:
                self.timer_task.cancel()
            except asyncio.CancelledError:
                # Expected: task was cancelled, nothing to do
                self.logger.logstate_info("Timer task cancelled")
        self.timer_task = None

    def _start_countdown_timer(self, current: int, total: int, reverse: bool):
        if self.timer_task is None:
            self.timer_task = self.entry.async_create_task(
                self.hass,
                self._count_down_time_out(current, total, reverse),
            )

    def process_panel_event_log(
        self,
        total: int,
        l_current: int,
        partition_val: set[int] | int,
        dateandtime: datetime,
        zoneStr: str,
        eventStr: str,
    ) -> None:
        """Process a sequence of panel log events in one pass."""

        current = l_current
        # Configuration options
        reverse: bool = to_bool(self.entry.options.get(CONF_LOG_REVERSE, False))
        max_entries: int = self.entry.options.get(CONF_LOG_MAX_ENTRIES, 1)
        total: int = min(total or max_entries, max_entries)
        if reverse:
            current = total + 1 - current

        # Initialize accumulators on first logentry
        if l_current == 1:
            self.collating_data = True
            self.xmldata: list[dict[str, Any]] = []
            self.csvdata: list[str] = []  # store CSV lines as list for easier reversing
            self.logger.logstate_debug("Panel Event Log - Processing")
            self._start_countdown_timer(current, total, reverse)  # start a timer
        elif not self.collating_data:
            # self.logger.logstate_info("Panel Event Log - something wrong, accumulators are none partway through, resetting %s", logentry.current)
            self._stop_countdown_timer()
            self.xmldata: list[dict[str, Any]] = []
            self.csvdata: list[str] = []
            self.save_task = None
            return
        elif l_current % BLOCK_OF_EVENTS == 0:
            # If a block of BLOCK_OF_EVENTS entries have not been received in BLOCK_OF_TIME seconds then timeout and save the files, quit the event sequence
            # stop and restart the timer, this provides a countdown timer after the last received log data
            #   only do it every BLOCK_OF_EVENTS messages, no need to do it for every message
            #self.logger.logstate_info(f"Panel log {logentry}")
            self._stop_countdown_timer()
            self._start_countdown_timer(current, total, reverse)  # start a timer

        # Accumulate template and CSV data
        dt = dateandtime
        if not isinstance(dateandtime, datetime):
            dt: datetime = datetime.fromtimestamp(dateandtime)
        self.xmldata.append(
            {
                "current": str(current),
                "date": dt.isoformat(),
                "partition": str(partition_val),
                "zone": zoneStr,
                "event": eventStr,
            }
        )
        self.csvdata.append(
            f"{current}, {total}, {partition_val}, {dt.date().isoformat()}, {dt.time().isoformat()}, {zoneStr}, {eventStr}"
        )

        # Fire HA event if enabled
        if to_bool(self.entry.options.get(CONF_LOG_EVENT, False)) and current <= total and self.create_ha_fire_event is not None:
            self.create_ha_fire_event(
                event_id=PanelCondition.PANEL_LOG_ENTRY,
                datadictionary={
                    "current": current,
                    "total": total,
                    "date": dateandtime,
                    "partition": partition_val,
                    "zone": zoneStr,
                    "event": eventStr,
                    "reverse": reverse,
                },
            )

        # Finish processing on last logentry
        finished: bool = (not reverse and current == total) or (
            reverse and current == 1
        )
        if self.save_task is None and finished:
            self._stop_countdown_timer()
            self.logger.logstate_debug("Panel Event Log - Received last logentry")
            self.save_task = self.entry.async_create_task(
                self.hass,
                self._savePanelEventLogFiles(current, total, reverse),
            )
        elif finished:
            self.logger.logstate_info(
                "Panel Event Log - Received more data after the last logentry or after the timeout, the save task is already running"
            )

    async def _savePanelEventLogFiles(self, available: int, total: int, reverse: bool):

        completed = total + 1 - available if reverse else available + 1

        # create a new XML file with the results
        def write_string_to_file(path: str, content: str, ftype: str) -> None:
            if not path:
                return
            try:
                fpath = Path(path)
                fpath.parent.mkdir(parents=True, exist_ok=True)
                with fpath.open("w", encoding="utf-8") as fh:
                    self.logger.logstate_debug(
                        "Panel Event Log - Writing %s file %s", ftype, fpath
                    )
                    fh.write(content.strip())
                self.logger.logstate_debug(
                    "Panel Event Log - %s file %s closed automatically", ftype, fpath
                )

            except OSError as err:
                self.logger.logstate_debug(
                    "Panel Event Log - Failed to write %s file %s: %s", ftype, path, err
                )
                self.logger.create_ha_notification(
                    AvailableNotifications.EVENTLOG,
                    f"Panel Event Log - Failed to write {ftype.upper()} file",
                )

        def blocking_save():
            xml_file_path = self.entry.options.get(CONF_LOG_XML_FN, "")
            if self.xmldata and len(xml_file_path) > 0:
                try:
                    # We're in a thread so take a copy just in case
                    xmldata = list(self.xmldata)
                    # Reverse if needed
                    if reverse:
                        xmldata.reverse()
                    self.logger.logstate_debug(
                        "Panel Event Log - Starting xml save filename %s   file loader path %s",
                        xml_file_path,
                        str(self.hass.config.path()),
                    )
                    template = self.jinja_env.get_template(TEXT_XML_LOG_FILE_TEMPLATE)
                    output = template.render(
                        entries=xmldata,
                        total=total,
                        available=str(completed),
                    )
                    write_string_to_file(xml_file_path, output, "xml")
                except (OSError, TemplateError):
                    self.logger.create_ha_notification(
                        AvailableNotifications.EVENTLOG,
                        "Panel Event Log - Failed to create xml content",
                    )

            csv_file_path = str(self.entry.options.get(CONF_LOG_CSV_FN, ""))
            if self.collating_data and len(csv_file_path) > 0:
                try:
                    # We're in a thread so take a copy just in case
                    csvdata = list(self.csvdata)
                    self.logger.logstate_debug(
                        "Panel Event Log - Starting csv save filename %s",
                        csv_file_path,
                    )
                    # Reverse if needed
                    if reverse:
                        csvdata.reverse()
                    # Join CSV lines into single string
                    content = "\n".join(csvdata) + "\n"
                    if to_bool(self.entry.options.get(CONF_LOG_CSV_TITLE, False)):
                        content = (
                            "current, total, partition, date, time, zone, event\n"
                            + content
                        )
                    write_string_to_file(csv_file_path, content, "csv")
                except (OSError, AttributeError, TypeError):
                    self.logger.create_ha_notification(
                        AvailableNotifications.EVENTLOG,
                        "Panel Event Log - Failed to create csv content",
                    )

        # Need to run in executor as it does blocking I/O to save the 2 files
        await self.hass.async_add_executor_job(blocking_save)

        # Fire completion event if configured
        if to_bool(self.entry.options.get(CONF_LOG_DONE, False)) and self.create_ha_fire_event is not None:
            self.logger.logstate_debug(
                "Panel Event Log - Firing Completion Event",
            )
            self.create_ha_fire_event(
                event_id=PanelCondition.PANEL_LOG_COMPLETE,
                datadictionary={
                    "total": total,
                    "available": completed,
                    "reverse": reverse,
                    "complete": total == completed,
                },
            )
        # Reset ready for next time
        self.collating_data = False
        self.csvdata = []
        self.xmldata = []
        self.save_task = None
        self.logger.logstate_debug("Panel Event Log - Complete")
