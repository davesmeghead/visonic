"""Control and access to alrm info."""

from typing import Any

from .classes import (
    Alarm,
    Camera,
    Event,
    FeatureSet,
    Location,
    Panel,
    PanelInfo,
    Process,
    Status,
    Trouble,
    User,
    WakeupSMS,
)
from .core import API
from .device_definitions import DEVICE_SUBTYPES, DEVICE_TYPES
from .devices import Device, GenericDevice


class AlarmSystem:
    """Class definition of the main alarm system."""

    def __init__(self, new_api: API):
        """Alarm system initialisation."""
        self.__api = new_api
        self.user_code = None

    # System properties
    @property
    def api(self):
        """Return the API for direct access."""
        return self.__api

    @property
    async def connected(self):
        """Check if the API server is connected to the alarm panel."""
        st = await self.get_status()
        return st.connected

    def get_user_code(self):
        """Get the user code."""
        return self.user_code

    async def access_grant(self, user_id, email):
        """Grant a user access to the alarm panel via the API."""
        return await self.__api.access_grant(user_id, email)

    async def access_revoke(self, user_id):
        """Revoke access to the alarm panel via the API for a user."""
        return await self.__api.access_revoke(user_id)

    async def activate_siren(self):
        """Activate the siren (sound the alarm)."""
        cmd = await self.__api.activate_siren()
        return cmd.get("process_token")

    async def disable_siren(self, mode="all"):
        """Disable the siren (mute the alarm)."""
        cmd = await self.__api.disable_siren(mode=mode)
        return cmd.get("process_token")

    async def arm_home(self, partition=-1, user_code : str | None = None):
        """Send Arm Home command to the alarm system."""
        cmd = await self.__api.arm_home(partition, user_code)
        return cmd.get("process_token")

    async def arm_away(self, partition=-1, user_code : str | None = None):
        """Send Arm Away command to the alarm system."""
        cmd = await self.__api.arm_away(partition, user_code)
        return cmd.get("process_token")

    async def arm_home_instant(self, partition=-1, user_code : str | None = None):
        """Send Arm Home command to the alarm system."""
        cmd = await self.__api.arm_home_instant(partition, user_code)
        return cmd.get("process_token")

    async def arm_away_instant(self, partition=-1, user_code : str | None = None):
        """Send Arm Away command to the alarm system."""
        cmd = await self.__api.arm_away_instant(partition, user_code)
        return cmd.get("process_token")

    async def disarm(self, partition=-1, user_code : str | None = None):
        """Send Disarm command to the alarm system."""
        cmd = await self.__api.disarm(partition, user_code)
        return cmd.get("process_token")

    async def get_alarms(self):
        """Return alarms."""
        alarms = await self.__api.get_alarms()
        return [Alarm(alarm) for alarm in alarms]

    async def get_smart_devices(self):
        """Return alarms."""
        return await self.__api.get_smart_devices()

    async def get_smart_devices_settings(self):
        """Return alarms."""
        return await self.__api.get_smart_devices_settings()

    async def get_alerts(self):
        """Return alerts."""
        return await self.__api.get_alerts()

    async def get_cameras(self):
        """Fetch all the devices that are available."""
        cameras = await self.__api.get_cameras()
        return [Camera(camera) for camera in cameras]

    async def make_video(self, device: int) -> dict[str, Any] | list[Any]:
        """Make a video."""
        cmd = await self.__api.make_video(device)
        return cmd.get("process_token")

    def _get_data_from_warnings(self, warnings: list[dict[str,str]] | None) -> tuple[bool, str | None , bool , bool , bool]:
        # set default values
        low_battery = False
        trouble = None
        tamper = False
        state = False
        bypass = False
        if warnings is not None:
            for warning in warnings:
                # dict with 3 entries
                t = warning.get("type", "")
                s = warning.get("severity", "")
                _m = warning.get("in_memory", "")
                if t == "BYPASS":
                    bypass = True
                elif t == "LOW_BATTERY":
                    low_battery = True
                #elif t == "1_WAY":
                #    zone_one_way = True
                elif t == "OPENED":
                    state = True
                elif t in ("TAMPER_MEMORY", "TAMPER"):
                    tamper = True
                elif s == "TROUBLE":
                    # if Trouble and the type is none of the above, then report it as the trouble
                    trouble = None if len(t) == 0 else t.lower()
        return low_battery, trouble, tamper, state, bypass

    async def get_devices(self) -> list[Device]:
        """Fetch all the devices that are available."""
        device_list = []
        devices: dict[str, Any] | list[dict[str, Any]] = await self.__api.get_devices()
        if isinstance(devices, dict):
            devices = [devices] # if a single dict then make it a list of 1
        for device in devices:
            low_battery, trouble, tamper, state, _bypass = self._get_data_from_warnings(device.get("warnings"))
            # bypass not used as it's in the "traits", but is here to stop it being tagged as a "trouble"
            device["low_battery"] = low_battery
            device["trouble"] = trouble
            device["tamper"] = tamper
            device["state"] = state
            if (device_class := DEVICE_SUBTYPES.get(device["subtype"])) or (device_class := DEVICE_TYPES.get(device["device_type"])):
                device["sensor_group"] = device_class.sensor_group
                device_list.append(device_class.device(device))
            else:
                device_list.append(GenericDevice(device))
        return device_list

    async def get_events(self, timestamp_hour_offset=2):
        """Get the last couple of events (60 events on my system)."""
        events = await self.__api.get_events()
        return [Event(event) for event in events]

    async def get_feature_set(self):
        """Fetch the get_feature_set associated with the alarm system."""
        feature_set = await self.__api.get_feature_set()
        return FeatureSet(feature_set)

    async def get_locations(self):
        """Fetch the locations associated with the alarm system."""
        locations = await self.__api.get_locations()
        return [Location(location) for location in locations]

    async def get_panel_info(self):
        """Fetch basic information about the alarm system."""
        gpi = await self.__api.get_panel_info()
        return PanelInfo(gpi)

    async def get_panels(self):
        """Fetch a list of panels associated with the user."""
        panels = await self.__api.get_panels()
        return [Panel(panel) for panel in panels]

    async def get_process_status(self, process_token):
        """Fetch the status information associated with a process token."""
        processes = await self.__api.get_process_status(process_token)
        return [Process(process) for process in processes]

    async def get_rest_versions(self):
        """Fetch the supported API versions."""
        return await self.__api.get_version()["rest_versions"]

    async def get_status(self):
        """Fetch the current state of the alarm system."""
        status = await self.__api.get_status()
        return Status(status)

    async def get_troubles(self):
        """Fetch all the troubles that are available."""
        troubles = await self.__api.get_troubles()
        return [Trouble(trouble) for trouble in troubles]

    async def get_auto_devices(self):
        """Fetch all the automation devices that are available."""
        auto_devices = await self.__api.get_auto_devices()
        # Need to create a class that accesses the returned dict as properties
        return auto_devices  # noqa: RET504

    async def get_users(self):
        """Fetch a list of users in the alarm system."""
        users = await self.__api.get_users()
        return [User(user) for user in users["users"]]

    async def get_wakeup_sms(self):
        """Fetch a list of users in the alarm system."""
        wakeup_sms = await self.__api.get_wakeup_sms()
        return WakeupSMS(wakeup_sms)

    async def panel_add(self, alias, panel_serial, master_user_code, access_proof=None):
        """Add a new alarm panel to the user account. A master user code is required."""
        return await self.__api.panel_add(alias, panel_serial, access_proof, master_user_code)

    async def panel_login(self, panel_serial, user_code):
        """Establish a connection between the alarm panel and the API server."""
        self.user_code = user_code
        return await self.__api.panel_login(panel_serial, user_code)

    async def panel_logout(self):
        """Logout for a specific panel."""
        return await self.__api.panel_logout()

    async def panel_rename(self, alias, panel_serial):
        """Rename an alarm panel."""
        return await self.__api.panel_rename(alias, panel_serial)

    async def panel_unlink(self, panel_serial, password, app_id):
        """Unlink an alarm panel from the user account."""
        return await self.__api.panel_unlink(panel_serial, password, app_id)

    async def password_reset(self, email):
        """Send a password reset link to the email address provided in the email argument."""
        return await self.__api.password_reset(email)

    async def password_reset_complete(self, reset_password_code, new_password):
        """Complete the password reset by entering the reset code received in the email and a new password."""
        return await self.__api.password_reset_complete(reset_password_code, new_password)["user_token"]

    async def set_bypass_zone(self, zone, set_enabled):
        """Enabled or disable zone bypassing (for example, bypass a sensor to disable it)."""
        return (await self.__api.set_bypass_zone(zone, set_enabled))["process_token"]

    async def set_name_user(self, user_id, name):
        """Set the name of a user by user ID."""
        return (await self.__api.set_name("USER", user_id, name))["process_token"]

    async def set_rest_version(self, version="latest"):
        """Set the REST version."""
        await self.__api.set_rest_version(version)

    async def set_user_code(self, user_id, user_code):
        """Set the code of a user by user ID."""
        return (await self.__api.set_user_code(user_code, user_id))["process_token"]
