"""Classes for Alarm Entities."""

from collections.abc import Callable
from dataclasses import dataclass
import functools
import inspect
from typing import Any

from .const import TEXT_UNKNOWN


# Decorator function to set string output to title case
def title_case(func: Any) -> Callable[..., str]:
    """Format to title case."""

    def wrapper(*args: Any, **kwargs: Any):
        if result := func(*args, **kwargs):
            return str(result).title()
        return result

    return wrapper


@dataclass
class BaseClass:
    """Base class."""

    _data: dict[str, Any]

    def __repr__(self) -> str:
        """String representation."""
        res = ""
        attrs = inspect.getmembers(self, lambda a: not inspect.isroutine(a))
        for i, attr in enumerate([attr for attr in attrs if self._is_property(attr)]):
            if i:
                res = res + ", "
            res = res + f"{attr[0]} = {getattr(self, attr[0])}"
        return f"{type(self).__name__}({res})"

    def __str__(self) -> str:
        """String representation."""
        res = {}
        attrs = inspect.getmembers(self, lambda a: not inspect.isroutine(a))
        for attr in [attr for attr in attrs if self._is_property(attr)]:
            res[attr[0]] = getattr(self, attr[0])
        return f"{type(self)!s}: {res}"

    def _is_property(self, attr : tuple[str, Any]) -> bool:
        if not ((attr[0].startswith("__") and attr[0].endswith("__")) or attr[0].startswith("_")):
            return True
        return False

    def _get_nested_key(self, path: str, *default: Any) -> Any:
        """Get key value in json by dotted notation or return default."""
        try:
            return functools.reduce(lambda x, y: x[y], path.split("."), self._data)
        except KeyError:
            if default:
                return default[0]
            return None


@dataclass
class Camera(BaseClass):
    """Class definition of an event in the alarm system."""

    @property
    @title_case
    def location(self) -> str | None:
        """Return camera location."""
        return self._data.get("location")

    @property
    def partitions(self) -> list[int]:
        """Return camera partitions."""
        return self._data.get("partitions", [])

    @property
    def preenroll(self) -> bool:
        """Pre-enroll camera."""
        return self._data.get("preenroll", False)

    @property
    def preview_path(self) -> str | None:
        """Get camera preview path."""
        return self._data.get("preview_path")

    @property
    def status(self) -> str | None:
        """Get camera status."""
        return self._data.get("status")

    @property
    def timestamp(self) -> str | None:
        """Get camera timestamp."""
        # TODO: Convert to datetime
        return self._data.get("timestamp")

    @property
    def zone(self) -> int | None:
        """Get camera zone id."""
        return self._data.get("zone")

    @property
    @title_case
    def zone_name(self) -> str | None:
        """Get camera zone name."""
        return self._data.get("zone_name")


@dataclass
class Event(BaseClass):
    """Class definition of an event in the alarm system."""

    # Event properties
    @property
    def id(self) -> int:  # pylint: disable=invalid-name
        """Event ID."""
        return self._data.get("event", 0)

    @property
    def type_id(self) -> int:
        """Event type ID."""
        return self._data.get("type_id", 0)

    @property
    @title_case
    def label(self) -> str | None:
        """Event label."""
        return self._data.get("label")

    @property
    def description(self) -> str | None:
        """Event description."""
        return self._data.get("description")

    @property
    @title_case
    def appointment(self) -> str | None:
        """Event appointment."""
        return self._data.get("appointment")

    @property
    def datetime(self) -> str | None:
        """Event datetime."""
        # TODO: make datetime
        return self._data.get("datetime")

    @property
    def video(self) -> bool:
        """Event has video."""
        return self._data.get("video", False)

    @property
    @title_case
    def device_type(self) -> str | None:
        """Event device type."""
        return self._data.get("device_type")

    @property
    def zone(self) -> int | None:
        """Event zone."""
        return self._data.get("zone")

    @property
    def partitions(self) -> list[int] | None:
        """Event partitions."""
        return self._data.get("partitions")

    @property
    def name(self) -> str | None:
        """Event name."""
        return self._data.get("name")


@dataclass
class FeatureSet(BaseClass):
    """Class definition of an event in the alarm system."""

    # Event properties
    @property
    def events_enabled(self):
        """Get if events enabled."""
        return self._get_nested_key("events.is_enabled")

    @property
    def datetime_enabled(self):
        """Get if datetime enabled."""
        return self._get_nested_key("datetime.is_enabled")

    @property
    def partitions_enabled(self):
        """Getof partitions enabled."""
        return self._get_nested_key("partitions.is_enabled")

    @property
    def partitions_has_labels(self):
        """Get if partitions labels enabled."""
        return self._get_nested_key("partitions.is_labels_enabled")

    @property
    def partitions_max_count(self):
        """Get max supported partitions."""
        return self._get_nested_key("partitions.max_partitions")

    @property
    def devices_enabled(self):
        """Get if devices enabled."""
        return self._get_nested_key("devices.is_enabled")

    @property
    def sirens_can_enable(self):
        """Get if can enable sirens."""
        return self._get_nested_key("sirens.can_enable")

    @property
    def sirens_can_disable(self):
        """Get if can disable sirens."""
        return self._get_nested_key("sirens.can_disable")

    @property
    def home_automation_devices_enabled(self):
        """Get if home automation devices enabled."""
        return self._get_nested_key("home_automation_devices.is_enabled")

    @property
    def state_enabled(self):
        """Get if state enabled."""
        return self._get_nested_key("state.is_enabled")

    @property
    def state_can_set(self):
        """Get if state can be set."""
        return self._get_nested_key("state.can_set")

    @property
    def state_can_get(self):
        """Get if state can be read."""
        return self._get_nested_key("state.can_get")

    @property
    def faults_enabled(self):
        """Get if faults enabled."""
        return self._get_nested_key("faults.is_enabled")

    @property
    def diagnostic_enabled(self):
        """Get if diagnostic enabled."""
        return self._get_nested_key("diagnostic.is_enabled")

    @property
    def wifi_enabled(self):
        """Get if wifi enabled."""
        return self._get_nested_key("wifi.is_enabled")


@dataclass
class Location(BaseClass):
    """Class definition of a location in the alarm system."""

    @property
    def id(self):  # pylint: disable=invalid-name
        """Location ID."""
        return self._data.get("hel_id")

    @property
    @title_case
    def name(self):
        """Location name."""
        return self._data.get("name")

    @property
    def is_editable(self):
        """Location is editable."""
        return self._data.get("is_editable")


@dataclass
class PanelInfoPartition(BaseClass):
    """Class to hold partition info."""

    @property
    def id(self) -> int | None:  # pylint: disable=invalid-name
        """Partition ID."""
        return self._data.get("id")

    @property
    def active(self) -> bool | None:
        """Get if partition active."""
        return self._data.get("active")

    @property
    def exit_delay_time(self) -> int | None:
        """Get partition exit delay."""
        return self._data.get("exit_delay_time")

    @property
    def state_set(self) -> str | None:
        """Get partition state."""
        return self._data.get("state_set")

    @property
    def name(self) -> str | None:
        """Get partition name."""
        return self._data.get("name")


@dataclass
class PanelInfoFeatures(BaseClass):
    """Class to hold panel features."""

    @property
    def video_on_demand(self) -> bool | None:
        """Get if supports video."""
        return self._data.get("video_on_demand")

    @property
    def alert(self) -> bool | None:
        """Get alerts."""
        return self._data.get("alert")

    @property
    def enabling_siren(self) -> bool | None:
        """Get if supports enabling siren."""
        return self._data.get("enabling_siren")

    @property
    def disabling_siren(self) -> bool | None:
        """Get if supports disabling siren."""
        return self._data.get("disabling_siren")

    @property
    def wi_fi_connection(self) -> bool | None:
        """Get if supports wifi."""
        return self._data.get("wi_fi_connection")

    @property
    def set_date_time(self) -> bool | None:
        """Get date time."""
        return self._data.get("set_date_time")

    @property
    def outputs_setup(self) -> bool | None:
        """Get outputs."""
        return self._data.get("outputs_setup")


@dataclass
class PanelInfo(BaseClass):
    """Class definition of the general alarm system information."""

    @property
    @title_case
    def bypass_mode(self) -> str | None:
        """Bypass Mode."""
        return self._data.get("bypass_mode")

    @property
    @title_case
    def current_user(self) -> str | None:
        """Current User."""
        return self._data.get("current_user")

    @property
    def local_wakeup_needed(self) -> bool | None:
        """Local Wakeup Needed."""
        return self._data.get("local_wakeup_needed")

    @property
    @title_case
    def manufacturer(self) -> str | None:
        """Manufacturer."""
        return self._data.get("manufacturer")

    @property
    @title_case
    def model(self) -> str | None:
        """Model name."""
        return self._data.get("model")

    @property
    def remote_admin_requires_user_acceptance(self) -> bool | None:
        """Programming requires user acceptance."""
        return self._data.get("remote_switch_to_programming_mode_requires_user_acceptance")

    @property
    def serial(self) -> str | None:
        """Serial no."""
        return self._data.get("serial")

    @property
    def multi_partitions(self) -> bool | None:
        """Multi partitions enabled."""
        p = self._data.get("partitions")
        return p and len(p) > 1

    @property
    def partitions(self) -> list[PanelInfoPartition]:
        """Partitions info."""
        p = self._data.get("partitions")
        return list([PanelInfoPartition(partition) for partition in p] if p else [])

    @property
    def features(self) -> PanelInfoFeatures:
        """Get panel features."""
        f = self._data.get("features")
        return PanelInfoFeatures(f or {})

@dataclass
class Panel(BaseClass):
    """Class definition of the general alarm system information."""

    # Panel properties
    @property
    def panel_serial(self) -> str | None:
        """Get panel serial."""
        return self._data.get("panel_serial")

    @property
    def alias(self) -> str | None:
        """Get panel alias."""
        return self._data.get("alias")


@dataclass
class Partition(BaseClass):
    """Class definition of a partition in the alarm system."""

    # Partition properties
    @property
    def id(self) -> int | None:  # pylint: disable=invalid-name
        """Get partition id."""
        return self._data.get("id")

    @property
    def state(self):
        """Get partition state."""
        return self._data.get("state")

    @property
    def status(self) -> str | None:
        """Get partition status."""
        return self._data.get("status")

    @property
    def ready(self) -> bool | None:
        """Get if partition ready."""
        return self._data.get("ready")

    @property
    def options(self) -> list[Any] | None:
        """Get partition options."""
        return self._data.get("options")


@dataclass
class Process(BaseClass):
    """Class definition of a process in the alarm system."""

    # Partition properties
    @property
    def token(self) -> str | None:
        """Get process token."""
        return self._data.get("token")

    @property
    def status(self) -> str | None:
        """Get process status."""
        return self._data.get("status")

    @property
    def message(self) -> str | None:
        """Get process message."""
        return self._data.get("message")

    @property
    def error(self) -> str | None:
        """Get process error."""
        return self._data.get("error")


@dataclass
class Status(BaseClass):
    """Class definition representing the status of the alarm system."""

    # Status properties
    @property
    def connected(self) -> bool | None:
        """Get if connected."""
        return self._data.get("connected")

    @property
    def bba_connected(self) -> bool:
        """Get if broadband connected."""
        return self._get_nested_key("connected_status.bba.is_connected", False)

    @property
    def bba_state(self) -> str:
        """Get broadband connection state."""
        return self._get_nested_key("connected_status.bba.state", TEXT_UNKNOWN)

    @property
    def gprs_connected(self) -> bool:
        """Get if GPRS connected."""
        return self._get_nested_key("connected_status.gprs.is_connected", False)

    @property
    def gprs_state(self) -> str:
        """Get GPRS state."""
        return self._get_nested_key("connected_status.grps.state", TEXT_UNKNOWN)

    @property
    def discovery_completed(self) -> bool:
        """Get if discovery completed."""
        return self._get_nested_key("discovery.completed")

    @property
    def discovery_stages(self) -> int:
        """Get discovery stages."""
        return self._get_nested_key("discovery.stages")

    @property
    def discovery_in_queue(self) -> int:
        """Get if discovery in queue."""
        return self._get_nested_key("discovery.in_queue")

    @property
    def discovery_triggered(self) -> bool:
        """Get if discovery triggered."""
        return self._get_nested_key("discovery.triggered")

    @property
    def partitions(self) -> list[Partition]:
        """Get partitions."""
        return [Partition(partition) for partition in self._data.get("partitions", [])]

    @property
    def rssi_level(self) -> int:
        """Get RSSI signal level."""
        return self._get_nested_key("rssi.level")

    @property
    def rssi_network(self) -> str:
        """Get RSSI signal network."""
        return self._get_nested_key("rssi.network")


@dataclass
class Alarm(BaseClass):
    """Class definition of Alarm."""

    @property
    def device_type(self) -> str | None:
        """Device type."""
        return self._data.get("device_type")

    @property
    def alarm_type(self) -> str | None:
        """Alarm type."""
        return self._data.get("alarm_type")

    @property
    def date_time(self) -> str | None:
        """Date time of alarm."""
        return self._data.get("datetime")

    @property
    def has_video(self) -> bool | None:
        """Has video."""
        return self._data.get("has_video")

    @property
    def event_id(self) -> int | None:
        """Event id."""
        return self._data.get("evt_id")

    @property
    @title_case
    def location(self) -> str | None:
        """Location."""
        return self._data.get("location")

    @property
    def partitions(self) -> list[int] | None:
        """Partitions."""
        return self._data.get("partitions")

    @property
    def zone(self) -> int | None:
        """Zone ID."""
        return self._data.get("zone")

    @property
    @title_case
    def zone_name(self) -> str | None:
        """Zone type."""
        return self._data.get("zone_name")

    @property
    def zone_type(self) -> str | None:
        """Zone type."""
        return self._data.get("zone_type")


@dataclass
class Trouble(BaseClass):
    """Class definition of a trouble in the alarm system."""

    # Trouble properties
    @property
    def device_type(self) -> str | None:
        """Device type."""
        return self._data.get("device_type")

    @property
    @title_case
    def location(self) -> str | None:
        """Location."""
        return self._data.get("location")

    @property
    def partitions(self) -> list[int] | None:
        """Partitions."""
        return self._data.get("partitions")

    @property
    def trouble_type(self) -> str | None:
        """Trouble type."""
        return self._data.get("trouble_type")

    @property
    def zone(self) -> int | None:
        """Zone ID."""
        return self._data.get("zone")

    @property
    @title_case
    def zone_name(self) -> str | None:
        """Zone type."""
        return self._data.get("zone_name")

    @property
    def zone_type(self) -> str | None:
        """Zone type."""
        return self._data.get("zone_type")


@dataclass
class User(BaseClass):
    """Class definition of a user in the alarm system."""

    # User properties
    @property
    def id(self) -> int | None:  # pylint: disable=invalid-name
        """User ID."""
        return self._data.get("id")

    @property
    @title_case
    def name(self) -> str | None:
        """User name."""
        return self._data.get("name")

    @property
    def email(self) -> str | None:
        """User email."""
        return self._data.get("email")

    @property
    def partitions(self) -> list[int] | None:
        """Device is active."""
        return self._data.get("partitions")


@dataclass
class WakeupSMS(BaseClass):
    """Class definition of a wakeup SMS in the alarm system."""

    # Wakeup SMS properties
    @property
    def phone_number(self) -> str | None:
        """Get SMS phone number."""
        return self._data.get("phone")

    @property
    def message(self) -> str | None:
        """Get sms message."""
        return self._data.get("sms")
