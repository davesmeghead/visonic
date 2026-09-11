"""Log Events.

# This class saves the relevant log statements for the diagnostics file
"""

from datetime import UTC, datetime
import logging
from typing import NamedTuple

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Any, HomeAssistant

from .const import (
    ALARM_COMMAND_EVENT,
    ALARM_PANEL_CHANGE_EVENT,
    ALARM_PANEL_LOG_FILE_COMPLETE,
    ALARM_PANEL_LOG_FILE_ENTRY,
    ALARM_SENSOR_CHANGE_EVENT,
    CONF_ALARM_NOTIFICATIONS,
    MAX_CLIENT_LOG_ENTRIES,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
    PANEL_ATTRIBUTE_NAME,
    PE_PARTITION,
)
from .utils import getAlarmPanelUniqueIdent, slugify
from .visonic_types import AvailableNotifications, PanelCondition


class HA_Event_Type(NamedTuple):
    """Represents a Home Assistant event type with a name and action."""
    name: str
    action: str

# fmt: off
AlarmPanelEventActionList: dict[PanelCondition, HA_Event_Type]= {
    PanelCondition.ZONE_UPDATE                : HA_Event_Type(ALARM_SENSOR_CHANGE_EVENT,     ""),
    PanelCondition.PANEL_UPDATE               : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "panelupdate"),
    PanelCondition.PANEL_RESET                : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "panelreset"),
    PanelCondition.IMAGE_UPDATE               : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "imageupdate"),
    PanelCondition.PIN_REJECTED               : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "pinrejected"),
    PanelCondition.DOWNLOAD_TIMEOUT           : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutdownload"),
    PanelCondition.WATCHDOG_TIMEOUT_GIVINGUP  : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutwaiting"),
    PanelCondition.WATCHDOG_TIMEOUT_RETRYING  : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "timeoutactive"),
    PanelCondition.NO_DATA_FROM_PANEL         : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "nopaneldata"),
    PanelCondition.DOWNLOAD_SUCCESS           : HA_Event_Type(None, None),
    PanelCondition.STARTUP_SUCCESS            : HA_Event_Type(None, None),
    PanelCondition.PUSH_CHANGE                : HA_Event_Type(None, None),
    PanelCondition.CONNECTION                 : HA_Event_Type(ALARM_PANEL_CHANGE_EVENT,      "connection"),
    PanelCondition.PANEL_LOG_COMPLETE         : HA_Event_Type(ALARM_PANEL_LOG_FILE_COMPLETE, ""),
    PanelCondition.PANEL_LOG_ENTRY            : HA_Event_Type(ALARM_PANEL_LOG_FILE_ENTRY,    ""),
    PanelCondition.CHECK_ARM_DISARM_COMMAND   : HA_Event_Type(ALARM_COMMAND_EVENT,           "armdisarm"),
    PanelCondition.CHECK_BYPASS_COMMAND       : HA_Event_Type(ALARM_COMMAND_EVENT,           "bypass"),
    PanelCondition.CHECK_EVENT_LOG_COMMAND    : HA_Event_Type(ALARM_COMMAND_EVENT,           "eventlog"),
    PanelCondition.CHECK_SWITCH_COMMAND       : HA_Event_Type(ALARM_COMMAND_EVENT,           "switch")
}
# fmt: on

###################################################################################
#####################  Log Output for Diagnostics use #############################
###################################################################################

class logEvents:
    """Log events to the diagnostics log."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        logger: logging.Logger,
        panel_id: int,
    ) -> None:
        """Initialise."""
        self.panel_id = panel_id
        self.logger = logger
        self.hass = hass
        self.entry = entry
        self.strlog: list[str] = []
        self.panel_entity_name: dict[int, str] = {}

    def logstate_debug(self, msg: str, *args: object, **kwargs: object) -> None:
        """Log debug state."""
        s: str = "P" + str(self.panel_id) + "  " + ((msg % args % kwargs) if (args or kwargs) else msg)
        # s = self.logger.info("P%s  " + msg, self.panel_id, *args)
        self.logger.debug(s)
        self.strlog.append(str(datetime.now(UTC).astimezone()) + "  D " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def logstate_info(self, msg: str, *args: object, **kwargs: object) -> None:
        """Log info state."""
        s: str = "P" + str(self.panel_id) + "  " + ((msg % args % kwargs) if (args or kwargs) else msg)
        self.logger.info(" %s", s)
        self.strlog.append(str(datetime.now(UTC).astimezone()) + "  I " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def logstate_warning(self, msg: str, *args: object, **kwargs: object) -> None:
        """Log warning state."""
        s: str = "P" + str(self.panel_id) + "  " + ((msg % args % kwargs) if (args or kwargs) else msg)
        self.logger.warning(s)
        self.strlog.append(str(datetime.now(UTC).astimezone()) + "  W " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def logstate_error(self, msg: str, *args: object, **kwargs: object) -> None:
        """Log error state."""
        s: str = "P" + str(self.panel_id) + "  " + ((msg % args % kwargs) if (args or kwargs) else msg)
        self.logger.error(s)
        self.strlog.append(str(datetime.now(UTC).astimezone()) + "  E " + s)
        while len(self.strlog) > MAX_CLIENT_LOG_ENTRIES:
            self.strlog.pop(0)

    def get_str_log(self):
        """Get string log."""
        return self.strlog

    def set_partition_name(
        self, partition: int | None = None, panel_entity_name: str | None = None
    ):
        """Set the partition naming for the alarm panel entities."""
        if (
            panel_entity_name is not None
            and partition is not None
            and 0 <= partition <= 2
        ):
            self.panel_entity_name[partition] = panel_entity_name

    def create_ha_notification(self, condition: AvailableNotifications, message: str):
        """Create a message in the log file and a notification on the HA Frontend."""
        notification_config = self.entry.options.get(CONF_ALARM_NOTIFICATIONS, [])
        self.logstate_debug(f"notification_config {notification_config}")
        if (
            condition == AvailableNotifications.ALWAYS
            or condition.value in notification_config
        ):
            # Create an info entry in the log file and an HA notification
            self.logstate_info(f"HA Notification: {condition}  {message}")
            persistent_notification.create(
                self.hass,
                message,
                title=NOTIFICATION_TITLE,
                notification_id=NOTIFICATION_ID,
            )
        else:
            # Just create a log file entry (but indicate that it wasnt shown in the frontend to the user
            self.logstate_info(
                f"HA Notification (not shown in frontend): {condition}  {message}"
            )

    def create_ha_fire_event(
        self,
        event_id: PanelCondition,
        datadictionary: dict[str, Any],
        entity_id: str | None = None,
    ) -> None:
        """Fire an HA Event in to HA with the associated data dictionary."""
        # Check to ensure variables are set correctly
        if self.hass is not None:
            # Event ID must be in the list to fire an HA event out
            if event_id in AlarmPanelEventActionList:
                name = AlarmPanelEventActionList[event_id].name
                if name is not None:
                    event_action = AlarmPanelEventActionList[event_id].action
                    # Base event dictionary
                    dd: dict[str, Any] = {
                        PANEL_ATTRIBUTE_NAME: self.panel_id,
                        **(datadictionary.copy() if datadictionary else {}),
                    }
                    # Include action if present
                    if event_action:
                        dd["action"] = event_action

                    if entity_id is None:
                        panel_id = f"{Platform.ALARM_CONTROL_PANEL}.{slugify(getAlarmPanelUniqueIdent(self.panel_id))}"
                    else:
                        panel_id = f"{Platform.ALARM_CONTROL_PANEL}.{entity_id}"
                    pe_part: set[int] | int | None = dd.get(PE_PARTITION)
                    candidates: list[int] | set[int] = (
                        [pe_part]
                        if isinstance(pe_part, int)
                        else pe_part if isinstance(pe_part, set) else []
                    )
                    # Add partition is set
                    partition_index = next(
                        (p for p in candidates if p in self.panel_entity_name), None
                    )
                    if partition_index is not None:
                        self.logstate_debug(
                            "Client [fire] pe_part=%s  panel_entity_name=%s",
                            pe_part,
                            self.panel_entity_name,
                        )
                        if entity_id is None:
                            panel_id = f"{Platform.ALARM_CONTROL_PANEL}.{slugify(self.panel_entity_name[partition_index])}"
                        else:
                            panel_id = f"{Platform.ALARM_CONTROL_PANEL}.{entity_id}"
                        dd[PE_PARTITION] = partition_index + 1
                    elif isinstance(pe_part, set):
                        dd.pop(PE_PARTITION, None)
                    # Add panel_id
                    dd["panel_id"] = panel_id
                    self.logstate_info(
                        "Client (panel %s) [fire] Sending HA Event %s  with data %s",
                        self.panel_id,
                        name,
                        dd,
                    )
                    # Fire the HA Event :)
                    self.hass.bus.fire(name, dd)
            else:
                # Capture invalid event_ids just in case
                self.logstate_warning(
                    "Attempt to generate HA event with unknown event_id %s",
                    event_id,
                )
        else:
            self.logstate_warning(
                "Attempt to generate HA event when hass is undefined"
            )
