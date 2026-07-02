"""Log Events.

# This class saves the relevant log statements for the diagnostics file
"""

from datetime import UTC, datetime
import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ALARM_NOTIFICATIONS,
    MAX_CLIENT_LOG_ENTRIES,
    NOTIFICATION_ID,
    NOTIFICATION_TITLE,
)
from .visonic_types import AvailableNotifications

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

    def get_str_log(self):
        """Get string log."""
        return self.strlog
