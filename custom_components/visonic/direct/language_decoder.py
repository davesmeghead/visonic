"""Language Translation."""

from typing import NamedTuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_translate_state

from ..const import DOMAIN  # noqa: TID252


class LanguageDecoder:
    """Decode language translations for panel events."""

    ###############################################################
    ######  Known Data Strings for EPROM and Message Decode  ######
    ###############################################################

    # Default "Panel" to English
    en_keys = [
        "system",
        "zone",
        "fob",
        "user",
        "pad",
        "siren",
        "2pad",
        "switch",
        "pgm",
        "gsm",
        "powerlink",
        "ptag",
        "repeater",
        "undefined",
    ]

    # pmax is powermax, pmas is powermaster
    class pmLogPowerColl(NamedTuple):
        """Log power collection configuration.

        Attributes:
        ----------
        key: str
            The key identifier for the log power entry.
        name: str
            The human-readable name for the log power entry.
        pmax_include: bool
            Whether to include this entry in PowerMax logs.
        pmax_autonumber: bool
            Whether to auto-number entries in PowerMax logs.
        pmax_start: int
            The starting number for PowerMax entries.
        pmax_stop: int
            The ending number for PowerMax entries.
        pmas_include: bool
            Whether to include this entry in PowerMaster logs.
        pmas_autonumber: bool
            Whether to auto-number entries in PowerMaster logs.
        pmas_start: int
            The starting number for PowerMaster entries.
        pmas_stop: int
            The ending number for PowerMaster entries.
        """

        key: str
        name: str
        pmax_include: bool
        pmax_autonumber: bool
        pmax_start: int
        pmax_stop: int
        pmas_include: bool
        pmas_autonumber: bool
        pmas_start: int
        pmas_stop: int

    # fmt: off

    # List all device types for the 2 main panel types. The values represent whether that panel supports that device and how many
    device_types = [ #                      PowerMax Settings           PowerMaster Settings     powermax   powermaster
        pmLogPowerColl( en_keys[0]  , "System" ,  True, False, 0,  0 ,  True,  False, 0,  0 ), #     0           0  System
        pmLogPowerColl( en_keys[1]  , "Zone"   ,  True,  True, 1, 30 ,  True,   True, 1, 64 ), #     1           1  Zone
        pmLogPowerColl( en_keys[2]  , "Fob"    ,  True,  True, 1,  8 ,  True,   True, 1, 32 ), #    31          65  Fob
        pmLogPowerColl( en_keys[3]  , "User"   ,  True,  True, 1,  8 ,  True,   True, 1, 48 ), #    39          97  User
        pmLogPowerColl( en_keys[4]  , "Pad"    ,  True,  True, 1,  8 ,  True,   True, 1, 32 ), #    47         145  Pad
        pmLogPowerColl( en_keys[5]  , "Sir"    ,  True,  True, 1,  2 ,  True,   True, 1,  8 ), #    55         177  Sir
        pmLogPowerColl( en_keys[6]  , "2Pad"   ,  True,  True, 1,  4 ,  True,   True, 1,  4 ), #    57         185  2PAD
        pmLogPowerColl( en_keys[7]  , "Switch" ,  True,  True, 1, 15 ,  True,   True, 1, 15 ), #    61         189  Switch
        pmLogPowerColl( en_keys[8]  , "PGM"    ,  True, False, 0,  0 ,  True,  False, 0,  0 ), #    76         204  PGM
        pmLogPowerColl( en_keys[9]  , "GSM"    ,  True, False, 0,  0 , False,  False, 0,  0 ), #    77            - GSM
        pmLogPowerColl( en_keys[10] , "P-LINK" ,  True, False, 0,  0 ,  True,  False, 0,  0 ), #    78         205  P-LINK
        pmLogPowerColl( en_keys[11] , "PTag"   ,  True,  True, 1,  8 ,  True,   True, 1, 32 ), #    79         206  PTag
        pmLogPowerColl( en_keys[12] , "Rptr"   , False, False, 0,  0 ,  True,   True, 1,  8 ), #     -         238  Rptr
        pmLogPowerColl( en_keys[13] , "Unknown",  True, False, 1, 41 ,  True,  False, 1, 10 )  #    87         246  Unknown
    ]

    # Use English as the default values unless updated by the settings from the Integration
    log_event = [
        "None",
        # 1
        "Interior Alarm", "Perimeter Alarm", "Delay Alarm", "24h Silent Alarm", "24h Audible Alarm",
        "Tamper", "Control Panel Tamper", "Tamper Alarm", "Tamper Alarm", "Communication Loss",
        # 11
        "Panic From Keyfob", "Panic From Control Panel", "Duress", "Confirm Alarm", "General Trouble",
        "General Trouble Restore", "Interior Restore", "Perimeter Restore", "Delay Restore", "24h Silent Restore",
        # 21
        "24h Audible Restore", "Tamper Restore", "Control Panel Tamper Restore", "Tamper Restore", "Tamper Restore",
        "Communication Restore", "General Restore", "Cancel Alarm", "Trouble Restore", "Not used",
        # 31
        "Recent Close", "Fire", "Fire Restore", "Not Active", "Emergency",
        "Remove User", "Disarm Latchkey", "Confirm Alarm Emergency", "Supervision (Inactive)", "Supervision Restore (Active)",
        # 41
        "Low Battery", "Low Battery Restore", "AC Fail", "AC Restore", "Control Panel Low Battery",
        "Control Panel Low Battery Restore", "RF Jamming", "RF Jamming Restore", "Communications Failure", "Communications Restore",
        # 51
        "Telephone Line Failure", "Telephone Line Restore", "Auto Test", "Fuse Failure", "Fuse Restore",
        "Keyfob Low Battery", "Keyfob Low Battery Restore", "Engineer Reset", "Battery Disconnect", "1-Way Keypad Low Battery",
        # 61
        "1-Way Keypad Low Battery Restore", "1-Way Keypad Inactive", "1-Way Keypad Restore Active", "Low Battery Ack", "Clean Me",
        "Fire Trouble", "Low Battery", "Battery Restore", "AC Fail", "AC Restore",
        # 71
        "Supervision (Inactive)", "Supervision Restore (Active)", "Gas Alert", "Gas Alert Restore", "Gas Trouble",
        "Gas Trouble Restore", "Flood Alert", "Flood Alert Restore", "X-10 Trouble", "X-10 Trouble Restore",
        # 81
        "Armed Home", "Armed Away", "Quick Armed Home", "Quick Armed Away", "Disarmed",
        "Fail To Auto-Arm", "Enter To Test Mode", "Exit From Test Mode", "Force Arm", "Auto Arm",
        # 91
        "Instant Arm", "Bypass", "Fail To Arm", "Door Open", "Communication Established By Control Panel",
        "System Reset", "Installer Programming", "Wrong Password", "Not Sys Event", "Not Sys Event",
        # 101
        "Extreme Hot Alert", "Extreme Hot Alert Restore", "Freeze Alert", "Freeze Alert Restore", "Human Cold Alert",
        "Human Cold Alert Restore", "Human Hot Alert", "Human Hot Alert Restore", "Temperature Sensor Trouble", "Temperature Sensor Trouble Restore",
        # 111
        # New values for PowerMaster and models with partitions
        "PIR Mask", "PIR Mask Restore", "Repeater low battery", "Repeater low battery restore", "Repeater inactive",
        "Repeater inactive restore", "Repeater tamper", "Repeater tamper restore", "Siren test end", "Devices test end",
        # 121
        "One way comm. trouble", "One way comm. trouble restore", "Sensor outdoor alarm", "Sensor outdoor restore", "Guard sensor alarmed",
        "Guard sensor alarmed restore", "Date time change", "System shutdown", "System power up", "Missed Reminder",
        # 131
        "Pendant test fail", "Basic KP inactive", "Basic KP inactive restore", "Basic KP tamper", "Basic KP tamper Restore",
        "Heat", "Heat restore", "LE Heat Trouble", "CO alarm", "CO alarm restore",
        # 141
        "CO trouble", "CO trouble restore", "Exit Installer", "Enter Installer", "Self test trouble",
        "Self test restore", "Confirm panic event", "", "Soak test fail", "Fire Soak test fail",
        # 151
        "Gas Soak test fail"
    ]

    # fmt: on

    def __init__(self, hass: HomeAssistant) -> None:
        """Init."""
        self.hass = hass

        # Create the defaults in English to be updated by settings from the Integration, derive the actuals from device_types
        self.user_log_power_max: list[str] = []
        self.user_log_power_master: list[str] = []
        for v in self.device_types:
            # create list
            if v.pmax_include:
                self.user_log_power_max.extend(
                    [
                        f"{v.name} {i:>02}" if v.pmax_autonumber else v.name
                        for i in range(v.pmax_start, v.pmax_stop + 1)
                    ]
                )
            if v.pmas_include:
                self.user_log_power_master.extend(
                    [
                        f"{v.name} {i:>02}" if v.pmas_autonumber else v.name
                        for i in range(v.pmas_start, v.pmas_stop + 1)
                    ]
                )

    def update(self):
        """Retrieve translated event names and actions from language translation files.

        Parameters
        ----------
        hass: HomeAssistant
            The Home Assistant instance.
        """
        ###################################################################################################
        # Retrieve the names of the things that create the events from the language translations files ####
        ###################################################################################################

        # For the event names, translate the keys i.e. map english key string to translations
        en_vals = {
            key: async_translate_state(
                hass=self.hass,
                domain=DOMAIN,
                device_class="event_name",
                state=key,
                platform=None,
                translation_key=None,
            )
            for key in self.en_keys
        }

        if len(en_vals) > 0:
            # Rebuild the device type in the language
            self.user_log_power_max = []
            self.user_log_power_master = []
            for vlp in self.device_types:
                # Use the translation if in the list else default back to the English.
                #     The translation file does not need to contain all 14 translations
                w = en_vals.get(vlp.key, vlp.name)  # get the translation
                # create list
                if vlp.pmax_include:
                    self.user_log_power_max.extend(
                        [
                            f"{w} {i:>02}" if vlp.pmax_autonumber else w
                            for i in range(vlp.pmax_start, vlp.pmax_stop + 1)
                        ]
                    )
                if vlp.pmas_include:
                    self.user_log_power_master.extend(
                        [
                            f"{w} {i:>02}" if vlp.pmas_autonumber else w
                            for i in range(vlp.pmas_start, vlp.pmas_stop + 1)
                        ]
                    )

        # Retrieve the actions from the language translations files, translate the log events
        for key in range(len(self.log_event)):
            state = f"{key:0>3}"
            tx_s = async_translate_state(
                hass=self.hass,
                domain=DOMAIN,
                device_class="event_action",
                state=state,
                platform=None,
                translation_key=None,
            )
            # Check to see if it's just returned the state that I passed in i.e. to make sure the translation exists
            if tx_s != state:
                self.log_event[key] = tx_s

    def getLogEventList(self) -> list[str]:
        """Get the complete log event list."""
        return self.log_event

    def get_event_entry(self, index: int | None) -> str:
        """Get log event entry."""
        if index is not None:
            if 0 <= index <= 151:
                if len(self.log_event[index]) > 0:
                    return self.log_event[index]
        return "Unknown"

    def getPowerMaxEntry(self, index: int | None) -> str:
        """Get powermax language entry."""
        if index is not None and index < len(self.user_log_power_max):
            return self.user_log_power_max[index]
        return "Unknown"

    def getPowerMasterEntry(self, index: int | None) -> str:
        """Get powermaster language entry."""
        if index is not None and index < len(self.user_log_power_master):
            return self.user_log_power_master[index]
        return "Unknown"

    def get_zone_entry(self, is_pm: bool, zone: int | None) -> str:
        """Return the string associated with the zone."""
        return self.getPowerMasterEntry(zone) if is_pm else self.getPowerMaxEntry(zone)
