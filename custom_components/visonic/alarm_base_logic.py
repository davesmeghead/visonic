"""Base class for alarm_control_panel and sensor entities.

Base logic for alarms: control panel or simple sensor

Note that either alarm_control_panel or sensor entities are create but never both.
This class is the base functionality for both
Sensor entities are created when the use selects emulation mode "Minimal Interaction (data only sent to obtain panel state)"
"""
from abc import abstractmethod
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, Entity
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
    VISONIC_TRANSLATION_KEY,
)
from .coordinator_base import VisonicCoordinator
from .exceptions import VisonicException
from .utils import to_bool
from .visonic_types import EmulationMode, PanelStateData, VisonicConfigData

_LOGGER = logging.getLogger(__name__)

class AlarmBaseLogic(CoordinatorEntity[VisonicCoordinator]):
    """Base type for common functions between sensor and alarm_control_panel."""

    """Panel and Partition Common."""

    def __init__(self, entry: ConfigEntry, partition: int | None, identifier: str):
        """Initialize a Visonic security alarm."""
        vce: VisonicConfigData = entry.runtime_data
        vc: VisonicCoordinator = vce.coordinator
        if vc is None:
            raise VisonicException("Alarm has been given invalid coordinator", 101)
        super().__init__(vc)
        self._entry = entry
        self._panel_id = vce.panel_id
        self.last_event_name = None

        self._partition = partition
        self._panel_ident: str = identifier
        if partition is None:
            # Partitions are not enabled in the panel, or we are still connecting to the panel and do not know yet
            self._partition_set = None
            self._name: str = self._panel_ident + "_main_panel"
            self.coordinator.log.logstate_info("[__init__] Setting primary sensor")
            #friendly_name = "Panel" if self._panel_id == 0 else f"Panel {self._panel_id}"
        else:
            # Partitions are enabled in the panel, this is the Entity for one of the partitons
            self._partition_set = {partition}
            # self._name : str = self._panel_ident + " Partition " + str(partition+1)        # Add 1 for user interface
            self._name: str = PARTITION_NAME_TEMPLATE.format(
                panel_ident=self._panel_ident, partition_index=partition + 1
            )
            self._attr_name = f"Partition {partition+1}" # if self._panel_id == 0 else f"Panel {self._panel_id} Partition {partition+1}"
            self.coordinator.set_partition_name(
                partition=partition, panel_entity_name=self._name
            )
            self.coordinator.log.logstate_info("[__init__] Setting partition %s", partition)

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
        self._update_config(entry)
        self.coordinator.ive_been_created()

    def _update_panel_state(self):
        if not self.coordinator:
            _LOGGER.warning(
                "Coordinator not ready for alarm panel %s yet it should be by now.",
                self._name,
            )
            self.panel_state_data = PanelStateData()
            return
        self.panel_state_data = self.coordinator.get_panel_and_partition_state(self._partition)

    @abstractmethod
    def update_local(self, entry: ConfigEntry) -> None:
        """Abstract base class for specific alarm and sensor devices to update."""

    def _update_config(self, entry: ConfigEntry):
        """Extract and update local variables from the configuration."""
        self.isarmhome = to_bool(entry.options.get(CONF_ARM_HOME_ENABLED, True))
        self.isarmnight = to_bool(entry.options.get(CONF_ARM_NIGHT_ENABLED, True))
        self.isarmhomeinstant = to_bool(entry.options.get(CONF_INSTANT_ARM_HOME, False))
        self.isarmawayinstant = to_bool(entry.options.get(CONF_INSTANT_ARM_AWAY, False))
        v = EmulationMode(entry.data.get(CONF_EMULATION_MODE, EmulationMode.POWERLINK))
        self.disable_all_panel_commands = v == EmulationMode.MINIMAL

    async def _handle_entry_update(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        # Re-read options
        self._entry = entry
        self._update_config(entry)
        self._update_panel_state()
        self.update_local(entry)
        # Trigger entity refresh if needed
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Called when this entity has been added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(self._entry.add_update_listener(self._handle_entry_update))

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

    @classmethod
    def alarm_and_sensor_common_setup(
        cls, entry: ConfigEntry, alarm: bool, piu: set[int] | None, identifier: str
    ) -> list[Entity]:
        """Common function that takes in all parameters to setup either an Alarm Control Panel or a Sensor."""
        # I import these in the classmethod so it should be acceptable. Otherwise there is a circular import.
        #    The alternative is to have this function repeated (almost) in VisonicAlarm and VisonicAlarmSensor
        #    The negative is that it returns a list of Entity type
        from .alarm_control_panel import VisonicAlarm  # noqa: PLC0415
        from .sensor import VisonicAlarmSensor  # noqa: PLC0415

        vce: VisonicConfigData = entry.runtime_data

        entities: list[Entity] = []

        # There can be a panel with no partitions    where piu = None
        # If a panel has partitions then piu is a set. Partitions are 1,2,3 but we use 0,1,2
        # The problem is, on first connection we don't know of any partitions, we only know when in powerlink/std+ "later"
        # So on first connection create one Alarm or Sensor Entity and assume no partitions (as not many people would use them)
        # If there are partitions "later", then
        #     1) Convert the single Entity to "dumb"
        #              The entity attributes are a collation from all other partitions
        #              The partition set is {0,1,2} i.e all partitions.  Commands such as Arm/Disarm then command all partitions
        #     2) Add new Alarm or Sensor Entities, one per partition
        #
        #  e.g. A panel with 2 partitions will have 3 Alarm or Sensor Entities.
        if piu is None and vce.alarm_entity is None:
            # No partitions and this is the first time the function has been called
            #   Make sure this is only done once, create the panel Entity
            vce.alarm_entity = (
                VisonicAlarm(entry=entry, partition=None, identifier=identifier)
                if alarm
                else VisonicAlarmSensor(entry=entry, partition=None, identifier=identifier)
            )
            entities.append(vce.alarm_entity)
        elif piu is not None and len(piu) > 1:
            # Partitions
            if vce.alarm_entity is None:
                # This should probably not happen i.e. partitions are known about straight away
                # Create the base/dumb panel first to command all partitions
                vce.alarm_entity = (
                    VisonicAlarm(entry=entry, partition=None, identifier=identifier)
                    if alarm
                    else VisonicAlarmSensor(entry=entry, partition=None, identifier=identifier)
                )
                vce.alarm_entity.set_as_base_panel()
                entities.append(vce.alarm_entity)
            else:
                # base/dumb panel entity already created (above) so just modify it
                vce.alarm_entity.set_as_base_panel()
            # Create an entity for each partition
            entities.extend(
                [
                    (
                        VisonicAlarm(entry=entry, partition=p, identifier=identifier)
                        if alarm
                        else VisonicAlarmSensor(entry=entry, partition=p, identifier=identifier)
                    )
                    for p in piu
                ]
            )
        return entities
