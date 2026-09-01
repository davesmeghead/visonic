"""Base class for alarm_control_panel and sensor entities.

Base logic for alarms: control panel or simple sensor

Note that either alarm_control_panel or sensor entities are create but never both.
This class is the base functionality for both
Sensor entities are created when the use selects emulation mode "Minimal Interaction (data only sent to obtain panel state)"
"""
from abc import abstractmethod
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    CONF_ARM_HOME_ENABLED,
    CONF_ARM_NIGHT_ENABLED,
    CONF_EMULATION_MODE,
    CONF_INSTANT_ARM_AWAY,
    CONF_INSTANT_ARM_HOME,
    DOMAIN,
    MANUFACTURER,
    PARTITION_ID_WHEN_BASE,
    PARTITION_NAME_TEMPLATE,
    PARTITION_NAME_TEMPLATE_SUFFIX,
    VISONIC_TRANSLATION_KEY,
)
from .coordinator_base import VisonicCoordinator
from .exceptions import VisonicException
from .utils import to_bool
from .visonic_data_types import VisonicPanelData
from .visonic_types import EmulationMode, PanelStateData

_LOGGER = logging.getLogger(__name__)

class AlarmBaseLogic(CoordinatorEntity[VisonicCoordinator]):
    """Base type for common functions between sensor and alarm_control_panel."""

    """Panel and Partition Common."""

    def __init__(self, entry: ConfigEntry, partition: int | None, identifier: str, show_keypad: bool | None = None):
        """Initialize a Visonic security alarm."""
        vcd: VisonicPanelData = entry.runtime_data
        vc: VisonicCoordinator = vcd.coordinator
        if vc is None:
            raise VisonicException("Alarm has been given invalid coordinator", 101)
        super().__init__(coordinator=vc)
        self._entry = entry
        self._panel_id = vcd.panel_id
        self.show_keypad = show_keypad
        self.last_event_name = None

        self._partition = partition
        if show_keypad is None:
            self._panel_ident: str = identifier
        elif show_keypad:
            self._panel_ident: str = identifier + "_keypad"
        else:
            self._panel_ident: str = identifier + "_nokeypad"
        if partition is None:
            # Partitions are not enabled in the panel, or we are still connecting to the panel and do not know yet
            self._partition_set = None
            self.coordinator.log.logstate_info("[__init__] Setting primary sensor")
            #friendly_name = "Panel" if self._panel_id == 0 else f"Panel {self._panel_id}"
            if show_keypad is None:
                self._attr_name = ""
                self._name: str = self._panel_ident + "_main_panel"
            elif show_keypad:
                self._attr_name = "Keypad"
                self._name: str = self._panel_ident + "_main_panel_keypad"
            else:
                self._attr_name = "No Keypad"
                self._name: str = self._panel_ident + "_main_panel_nokeypad"
        else:
            # Partitions are enabled in the panel, this is the Entity for one of the partitons
            self._partition_set = {partition}
            # self._name : str = self._panel_ident + " Partition " + str(partition+1)        # Add 1 for user interface
            if show_keypad is None:
                self._attr_name = f"Partition {partition+1}"
                self._name: str = PARTITION_NAME_TEMPLATE.format(
                    panel_ident=self._panel_ident, partition_index=partition + 1
                )
            elif show_keypad:
                self._attr_name = f"Partition {partition+1} (Keypad)"
                self._name: str = PARTITION_NAME_TEMPLATE_SUFFIX.format(
                    panel_ident=self._panel_ident, partition_index=partition + 1, suffix="Keypad"
                )
            else:
                self._attr_name = f"Partition {partition+1} (No Keypad)"
                self._name: str = PARTITION_NAME_TEMPLATE_SUFFIX.format(
                    panel_ident=self._panel_ident, partition_index=partition + 1, suffix="No Keypad"
                )
            self.coordinator.set_partition_name(
                partition=partition, panel_entity_name=self._name
            )
            self.coordinator.log.logstate_info("[__init__] Setting partition %s", partition+1)

        self._attr_unique_id = slugify(self._name)
        self._attr_should_poll = False
        self._attr_translation_key = VISONIC_TRANSLATION_KEY
        self.armcode = "Unknown"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
        )
        self.panel_state_data: PanelStateData = PanelStateData()
        self.update_local(entry)
        self.coordinator.ive_been_created()

    def _update_panel_state(self):
        if not self.coordinator:
            _LOGGER.warning(
                "Coordinator not ready for alarm panel %s yet it should be by now.",
                self._name,
            )
            self.panel_state_data = PanelStateData()
            return
        self.panel_state_data = self.coordinator.get_panel_and_partition_state(self._partition, self.show_keypad)

    @abstractmethod
    def update_local(self, entry: ConfigEntry) -> None:
        """Abstract base class for specific alarm and sensor devices to update."""

    @property
    def isarmhome(self) -> bool:  # noqa: D102
        return to_bool(self._entry.options.get(CONF_ARM_HOME_ENABLED, True))

    @property
    def isarmnight(self) -> bool:  # noqa: D102
        return to_bool(self._entry.options.get(CONF_ARM_NIGHT_ENABLED, True))

    @property
    def isarmhomeinstant(self) -> bool:  # noqa: D102
        return to_bool(self._entry.options.get(CONF_INSTANT_ARM_HOME, False))

    @property
    def isarmawayinstant(self) -> bool:  # noqa: D102
        return to_bool(self._entry.options.get(CONF_INSTANT_ARM_AWAY, False))

    @property
    def disable_all_panel_commands(self) -> bool:  # noqa: D102
        v = EmulationMode(self._entry.data.get(CONF_EMULATION_MODE, EmulationMode.POWERLINK))
        return v == EmulationMode.MINIMAL

    @callback
    def _handle_coordinator_update(self) -> None:
        """Get the state of the device."""
        self._update_panel_state()
        self.update_local(self._entry)
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available and that the coordinator has had an update."""
        return self._attr_available and self.coordinator.last_update_success

    def set_as_base_panel(self):
        """Reset the partition this entity is monitoring."""
        # Partitions are enabled in the panel, this is the base/dumb Entity
        self._partition = PARTITION_ID_WHEN_BASE
        # For the base panel we command (Arm, Disarm etc) all partitions
        self._partition_set = {0, 1, 2}
        #self._attr_name = "Main"
        self.coordinator.log.logstate_info(
            "Setting dumb panel  %s   %s",
            self._name,
            self._attr_unique_id,
        )
