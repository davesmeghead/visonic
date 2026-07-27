"""Types used in sending raw data to the panel."""


import asyncio
from typing import Any, NamedTuple

from .py_const import OBFUS, DebugLevel
from .py_enum import B0SubType, Receive, Send
from .py_exception import PyVisonicException
from .py_utils import convert_bytearray, get_local_time, toString

###################################################################################
##########################  Messages that we can send to the panel  ###############
###################################################################################

# A gregorian year, on average, contains 365.2425 days
#    Thus, expressed as seconds per average year, we get 365.2425 * 24 * 60 * 60 = 31,556,952 seconds/year
# use a named tuple for data and acknowledge
#    replytype   is a message type from the Panel that we should get in response
#    waitforack, if True means that we should wait for the acknowledge from the Panel before progressing
#    debugprint  If False then do not log the full raw data as it may contain the user code
#    waittime    a number of seconds after sending the command to wait before sending the next command

class VisonicCommand(NamedTuple):
    """Visonic Command Structure."""
    data: bytearray | bytes
    replytype: list[Any] | None
    waitforack: bool
    download: bool
    debugprint: DebugLevel
    waittime: float
    msg: str

# Debug Settings (what information to put in the log files) - Sending Messages to the Panel
SendDebugC = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug sending control messages
SendDebugM = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug sending message data
SendDebugD = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug sending EPROM message data
SendDebugI = DebugLevel.NONE if OBFUS else DebugLevel.FULL  # Debug sending image data

#VisonicCommand = collections.namedtuple('VisonicCommand', 'data replytype waitforack download debugprint waittime msg')
pmSendMsg: dict[Any, VisonicCommand] = {
    #                        data                                                                              replytype            waitforack download   debugprint waittime   msg
    # Quick command codes to start and stop download/powerlink are a single value
    Send.BUMP         : VisonicCommand(convert_bytearray('09')                                          , [Receive.PANEL_INFO]        , False, False,      SendDebugM, 0.5, "Bump Panel Data From Panel" ),  # Bump to try to get the panel to send a 3C
    Send.START        : VisonicCommand(convert_bytearray('0A')                                          , [Receive.LOOPBACK_TEST]     , False, False,      SendDebugM, 0.0, "Start" ),                          # waiting for STOP from panel for download complete
    Send.STOP         : VisonicCommand(convert_bytearray('0B')                                          , None                        , False, False,      SendDebugM, 1.5, "Stop" ),
    Send.EXIT         : VisonicCommand(convert_bytearray('0F')                                          , None                        , False, False,      SendDebugM, 1.5, "Exit" ),

    # Command codes do not have the Packet.POWERLINK_TERMINAL (0x43) on the end and are only 11 values
    Send.DOWNLOAD_DL  : VisonicCommand(convert_bytearray('24 00 00 99 99 00 00 00 00 00 00')            , None                        , False,  True,      SendDebugD, 0.0, "Start Download Mode" ),            # This gets either an acknowledge OR an Access Denied response
    Send.DOWNLOAD_TIME: VisonicCommand(convert_bytearray('24 00 00 99 99 00 00 00 00 00 00')            , None                        , False, False,      SendDebugD, 0.5, "Trigger Panel To Set Time" ),      # Use this instead of BUMP as can be used by all panels. To set time.
    Send.PANEL_DETAILS: VisonicCommand(convert_bytearray('24 00 00 99 99 00 00 00 00 00 00')            , [Receive.PANEL_INFO]        , False, False,      SendDebugD, 0.5, "Trigger Panel Data From Panel" ),  # Use this instead of BUMP as can be used by all panels
    Send.WRITE        : VisonicCommand(convert_bytearray('3D 00 00 00 00 00 00 00 00 00 00')            , None                        , False, False,      SendDebugD, 0.0, "Write Data Set" ),
    Send.DL           : VisonicCommand(convert_bytearray('3E 00 00 00 00 B0 00 00 00 00 00')            , [Receive.DOWNLOAD_BLOCK]    ,  True, False,      SendDebugD, 0.0, "Download Data Set" ),
    Send.SETTIME      : VisonicCommand(convert_bytearray('46 F8 00 01 02 03 04 05 06 FF FF')            , None                        , False, False,      SendDebugM, 1.0, "Setting Time" ),                   # may not need an ack so I don't wait for 1 and just get on with it
    Send.SER_TYPE     : VisonicCommand(convert_bytearray('5A 30 04 01 00 00 00 00 00 00 00')            , [Receive.DOWNLOAD_SETTINGS] , False, False,      SendDebugM, 0.0, "Get Serial Type" ),

    Send.EVENTLOG     : VisonicCommand(convert_bytearray('A0 00 00 00 99 99 00 00 00 00 00 43')         , [Receive.EVENT_LOG]         , False, False,      SendDebugC, 0.0, "Retrieving Event Log" ),
    Send.ARM          : VisonicCommand(convert_bytearray('A1 00 00 99 99 99 07 00 00 00 00 43')         , None                        ,  True, False,      SendDebugC, 0.0, "(Dis)Arming System" ),             # Including 07 to arm all 3 partitions
    Send.MUTE_SIREN   : VisonicCommand(convert_bytearray('A1 00 00 0B 99 99 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugC, 0.0, "Mute Siren" ),
    Send.STATUS       : VisonicCommand(convert_bytearray('A2 00 00 3F 00 00 00 00 00 00 00 43')         , [Receive.STATUS_UPDATE]     ,  True, False,      SendDebugM, 0.0, "Getting Status" ),                 # Ask for A5 messages, the 0x3F asks for 01 02 03 04 05 06 messages
    Send.STATUS_SEN   : VisonicCommand(convert_bytearray('A2 00 00 08 00 00 00 00 00 00 00 43')         , [Receive.STATUS_UPDATE]     ,  True, False,      SendDebugM, 0.0, "Getting A5 04 Status" ),           # Ask for A5 messages, the 0x08 asks for 04 message only
    Send.BYPASSTAT    : VisonicCommand(convert_bytearray('A2 00 00 20 00 00 00 00 00 00 00 43')         , [Receive.STATUS_UPDATE]     , False, False,      SendDebugC, 0.0, "Get Bypass and Enrolled Status" ), # Ask for A5 06 message (Enrolled and Bypass Status)
    Send.ZONENAME     : VisonicCommand(convert_bytearray('A3 00 00 00 00 00 00 00 00 00 00 43')         , [Receive.ZONE_NAMES]        ,  True, False,      SendDebugM, 0.0, "Requesting Zone Names" ),          # We expect 4 or 8 (64 zones) A3 messages back but at least get 1
    Send.SWITCH       : VisonicCommand(convert_bytearray('A4 00 00 00 00 00 99 99 99 00 00 43')         , None                        , False, False,      SendDebugM, 0.0, "Switch Data" ),                    # Retrieve Switch data
    Send.ZONETYPE     : VisonicCommand(convert_bytearray('A6 00 00 00 00 00 00 00 00 00 00 43')         , [Receive.ZONE_TYPES]        ,  True, False,      SendDebugM, 0.0, "Requesting Zone Types" ),          # We expect 4 or 8 (64 zones) A6 messages back but at least get 1

    Send.BYPASSEN     : VisonicCommand(convert_bytearray('AA 99 99 12 34 56 78 00 00 00 00 43')         , None                        , False, False,      SendDebugM, 0.0, "BYPASS Enable" ),                  # Bypass sensors
    Send.BYPASSDI     : VisonicCommand(convert_bytearray('AA 99 99 00 00 00 00 12 34 56 78 43')         , None                        , False, False,      SendDebugM, 0.0, "BYPASS Disable" ),                 # Arm Sensors (cancel bypass)

    Send.GETTIME      : VisonicCommand(convert_bytearray('AB 01 00 00 00 00 00 00 00 00 00 43')         , [Receive.POWERLINK]         ,  True, False,      SendDebugM, 0.0, "Get Panel Time" ),                 # Returns with an AB 01 message back
    Send.ALIVE        : VisonicCommand(convert_bytearray('AB 03 00 00 00 00 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 0.0, "I'm Alive Message To Panel" ),
    Send.RESTORE      : VisonicCommand(convert_bytearray('AB 06 00 00 00 00 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 0.0, "Restore Connection" ),             # It can take multiple of these to put the panel back in to powerlink
    Send.ENROL        : VisonicCommand(convert_bytearray('AB 0A 00 00 99 99 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 2.5, "Auto-Enrol PowerMax/Master" ),     # should get a reply of [0xAB] but its not guaranteed
    Send.INIT         : VisonicCommand(convert_bytearray('AB 0A 00 01 00 00 00 00 00 00 00 43')         , None                        ,  True, False,      SendDebugM, 3.0, "Init PowerLink Connection" ),
    # Send.IMAGE_FB     : VisonicCommand(convert_bytearray('AB 0E 00 17 1E 00 00 03 01 05 00 43')         , None                        ,  True, False,      SendDebugM, 0.0, "PowerMaster after jpg feedback" ),

    Send.SWITCH_NAMES : VisonicCommand(convert_bytearray('AC 00 00 00 00 00 00 00 00 00 00 43')         , [Receive.SWITCH_NAMES]      , False, False,      SendDebugM, 0.0, "Requesting Switch Names" ),
    #Send.GET_IMAGE    : VisonicCommand(convert_bytearray('AD 99 99 0A FF FF 00 00 00 00 00 43')         , [Receive.IMAGE_MGMT]        ,  True, False,      SendDebugI, 0.0, "Requesting JPG Image" ),           #
    Send.GET_IMAGE    : VisonicCommand(convert_bytearray('AD 0B 99 99 FF FF 00 00 00 00 00 43')         , [Receive.IMAGE_MGMT]        ,  True, False,      SendDebugI, 0.0, "Requesting JPG Image" ),           # Request a jpg image, first 99 is the zone, the second 99 is the number of images.
    # DISCONNECT_MESSAGE = "0d ad 0a 00 00 00 00 00 00 00 00 00 43 05 0a"

    # Acknowledges
    Send.ACK          : VisonicCommand(convert_bytearray('02')                                          , None                        , False, False, DebugLevel.NONE, 0.0, "Ack" ),
    Send.ACK_PLINK    : VisonicCommand(convert_bytearray('02 43')                                       , None                        , False, False, DebugLevel.NONE, 0.0, "Ack Powerlink" ),

    # PowerMaster specific
    #Send.PM_REQUEST   : VisonicCommand(convert_bytearray('B0 01 99 01 05 43')                           , [Receive.POWERMASTER]       ,  True, False,      SendDebugM, 0.0, "Powermaster Request Type 1" ),       # Request a message type from the panel, change 99 with the message type
    #Send.PM_REQUEST54 : VisonicCommand(convert_bytearray('B0 01 54 00 43')                              , [Receive.POWERMASTER]       ,  True, False,      SendDebugM, 0.0, "Powermaster Request a 54" ),         # Request a 54 message type from the panel
    #Send.PM_REQUEST58 : VisonicCommand(convert_bytearray('B0 01 58 00 43')                              , [Receive.POWERMASTER]       ,  True, False,      SendDebugM, 0.0, "Powermaster Request a 58" ),         # Request a 58 message type from the panel
    Send.PM_KEEPALIVE : VisonicCommand(convert_bytearray('B0 01 6A 00 43')                              , None                        ,  True, False,      SendDebugM, 0.0, "Powermaster Keep Alive Request" ),   # Request a Keep Alive from the panel 6A

    Send.PM_SIREN_MODE: VisonicCommand(convert_bytearray('B0 00 47 09 99 99 00 FF 08 0C 02 99 07 43')   , None                        ,  True, False,      SendDebugM, 0.0, "Powermaster Trigger Siren Mode" ),   # Trigger Siren, the 99 99 needs to be the usercode, other 99 is Siren Type
    Send.PM_SIREN     : VisonicCommand(convert_bytearray('B0 00 3E 0A 99 99 05 FF 08 02 03 00 00 01 43'), None                        ,  True, False,      SendDebugM, 1.0, "Powermaster Trigger Siren" ),        # Trigger Siren, the 99 99 needs to be the usercode
    Send.PL_BRIDGE    : VisonicCommand(convert_bytearray('E1 99 99 43')                                 , None                        , False, False,      SendDebugM, 0.0, "Powerlink Bridge" ),                 # Command to the Bridge

    Send.PM_SETBAUD   : VisonicCommand(convert_bytearray('B0 00 41 0D AA AA 01 FF 28 0C 05 01 00 BB BB 00 05 43'), None               ,  True, False,      SendDebugC, 2.5, "Powermaster Set Serial Baud Rate" ),

    # Not sure what these do to the panel. Panel replies with powerlink ack Packet.POWERLINK_TERMINAL 0x43
    #   Send.MSG4             : VisonicCommand(convert_bytearray('04 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 04 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
    #   Send.MSGC             : VisonicCommand(convert_bytearray('0C 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 0C 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
    #   Send.UNKNOWN_0E       : VisonicCommand(convert_bytearray('0E')                                          , None   , False, False,      SendDebugM, 0.0, "Message 0E.    Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
    #   Send.MSGE             : VisonicCommand(convert_bytearray('0E 43')                                       , None   , False, False,      SendDebugM, 0.0, "Message 0E 43. Not sure what this does to the panel. Panel replies with powerlink ack 0x43." ),
}

class B0_SendMessageTupleTmp(NamedTuple):
    """B0_SendMessageTupleTmp Command Structure."""
    data: int | B0SubType
    chunky: bool
    paged: bool

# B0 Messages subset that we can send to a Powermaster, embed within MSG_POWERMASTER to use
#B0_SendMessageTupleTmp = collections.namedtuple('B0_SendMessageTupleTmp', 'data chunky paged')


# Subclass the namedtuple to add a custom __str__ method
class B0_SendMessageTuple(B0_SendMessageTupleTmp):
    """Send message B0 Tuple."""
    def __str__(self):
        """Convert to string."""
        if isinstance(self.data, int):
            return f"Chunky={self.chunky} Paged={self.paged} Subtype={self.data}"
        if isinstance(self.data, B0SubType):
            return f"Chunky={self.chunky} Paged={self.paged} Subtype={self.data.name}"
        return "B0_SendMessageTuple unknown type"

pmSendMsgB0 = {   #                                      data  chunky paged
    B0SubType.WIRELESS_DEV_UPDATING : B0_SendMessageTuple(0x02,  True, False),
    B0SubType.WIRELESS_DEV_CHANNEL  : B0_SendMessageTuple(0x04,  True, False),
    B0SubType.INVALID_COMMAND       : B0_SendMessageTuple(0x06, False, False),      # This isn't chunked  INVALID_COMMAND
    B0SubType.ZONE_STAT07           : B0_SendMessageTuple(0x07,  True, False),
    B0SubType.WIRELESS_DEV_INACTIVE : B0_SendMessageTuple(0x08,  True, False),
    B0SubType.WIRELESS_DEV_MISSING  : B0_SendMessageTuple(0x09,  True, False),
    B0SubType.TAMPER_ACTIVITY       : B0_SendMessageTuple(0x0A,  True, False),      # Mark: Tamper Activities
    B0SubType.TAMPER_ALERT          : B0_SendMessageTuple(0x0B,  True, False),      # Mark: Tamper Alert
    B0SubType.WIRELESS_DEV_ONEWAY   : B0_SendMessageTuple(0x0E,  True, False),
    B0SubType.PANEL_STATE_2         : B0_SendMessageTuple(0x0F, False, False),      # Panel State 2, confirmed as not chunky
    B0SubType.TRIGGERED_ZONE        : B0_SendMessageTuple(0x13,  True, False),      # Triggered Zone ... maybe????????????????  0d b0 03 13 0d ff 01 03 08 00 00 00 00 00 00 00 00 da 43 02 0a  Decoded Chunk type 3   subtype 19   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.ZONE_OPENCLOSE        : B0_SendMessageTuple(0x18,  True, False),      # Sensor Open/Close State
    B0SubType.ZONE_BYPASS           : B0_SendMessageTuple(0x19,  True, False),      # Sensor Bypass
    B0SubType.SENSOR_UNKNOWN_1C     : B0_SendMessageTuple(0x1C,  True, False),      # Sensors UNKNOWN ...  ???????????????????  0d b0 03 1c 0d ff 01 03 08 00 00 00 00 00 00 00 00 db 43 f7 0a  Decoded Chunk type 3   subtype 28   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.SENSOR_ENROL          : B0_SendMessageTuple(0x1D,  True, False),      # Sensors Enrolment
    B0SubType.DEVICE_TYPES          : B0_SendMessageTuple(0x1F,  True, False),      # Sensors
    B0SubType.ASSIGNED_PARTITION    : B0_SendMessageTuple(0x20,  True,  True),
    B0SubType.ZONE_NAMES            : B0_SendMessageTuple(0x21,  True, False),      # Zone Names
    B0SubType.SYSTEM_CAP            : B0_SendMessageTuple(0x22,  True, False),      # System
    B0SubType.PANEL_STATE_1         : B0_SendMessageTuple(0x24,  True, False),      # Panel State
    B0SubType.WIRED_STATUS_1        : B0_SendMessageTuple(0x27,  True, False),
    B0SubType.WIRED_STATUS_2        : B0_SendMessageTuple(0x28,  True, False),
    B0SubType.EVENT_LOG             : B0_SendMessageTuple(0x2A,  True,  True),      # Event Log
    B0SubType.ZONE_TYPES            : B0_SendMessageTuple(0x2D,  True, False),      # Zone Types
    B0SubType.SENSOR_UNKNOWN_30     : B0_SendMessageTuple(0x30,  True, False),      # Sensors UNKNOWN ...  ???????????????????   0d b0 03 30 0d ff 01 03 08 00 00 00 00 00 00 00 00 dd 43 e1 0a  Decoded Chunk type 3   subtype 48   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.SENSOR_UNKNOWN_32     : B0_SendMessageTuple(0x32,  True, False),      # Sensors UNKNOWN ...  ???????????????????   0d b0 03 32 0d ff 01 03 08 00 00 00 00 00 00 00 00 de 43 de 0a  Decoded Chunk type 3   subtype 50   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.SENSOR_UNKNOWN_34     : B0_SendMessageTuple(0x34,  True, False),      # Sensors UNKNOWN ...  ???????????????????   0d b0 03 34 0d ff 01 03 08 00 00 00 00 00 00 00 00 df 43 db 0a  Decoded Chunk type 3   subtype 52   sequence 255  datasize 1    length 8    index ZONES            data 00 00 00 00 00 00 00 00
    B0SubType.PANEL_SETTINGS_35     : B0_SendMessageTuple(0x35,  True, False),
    B0SubType.LEGACY_EVENT_LOG      : B0_SendMessageTuple(0x36,  True, False),
    B0SubType.ASK_ME_1              : B0_SendMessageTuple(0x39,  True, False),      # Panel sending a list of message types that may have updated info
    B0SubType.PANEL_STATE_3         : B0_SendMessageTuple(0x3B,  True, False),
    B0SubType.ZONE_TEMPERATURE      : B0_SendMessageTuple(0x3D,  True, False),      # Zone Temperatures
#    "WIRELESS_DEVICES_40"       : B0_SendMessageTuple(0x40,  True, False),
    B0SubType.PANEL_SETTINGS_42     : B0_SendMessageTuple(0x42,  True, False),
    B0SubType.ZONE_LAST_EVENT       : B0_SendMessageTuple(0x4B,  True,  True),      # Zone Last Event. Paged for more than 30 sensors.
    B0SubType.ASK_ME_2              : B0_SendMessageTuple(0x51,  True, False),      # Panel sending a list of message types that may have updated info

    B0SubType.PANEL_STATE_4         : B0_SendMessageTuple(0x37,  True, False),
    B0SubType.PANEL_STATE_5         : B0_SendMessageTuple(0x38,  True, False),
    #B0SubType.PANEL_STATE_6         : B0_SendMessageTuple(0x3C,  True, False),

    # Not currently used, experimentation only
    B0SubType.DEVICE_COUNTS         : B0_SendMessageTuple(0x52,  True, False),
    B0SubType.WIRED_DEVICES         : B0_SendMessageTuple(0x53,  True, False),      # SWITCHES and PGM
    B0SubType.TROUBLES              : B0_SendMessageTuple(0x54,  True, False),      # 8 blocks of 9 bytes
    B0SubType.REPEATERS_55          : B0_SendMessageTuple(0x55,  True, False),
    B0SubType.DEVICE_INFO           : B0_SendMessageTuple(0x58,  True, False),
    B0SubType.GSM_STATUS            : B0_SendMessageTuple(0x59,  True, False),
    B0SubType.KEYPADS               : B0_SendMessageTuple(0x5b,  True, False),
    B0SubType.DEVICES_5D            : B0_SendMessageTuple(0x5d,  True, False),      # PM10 gave invalid
    B0SubType.SOFTWARE_VERSION      : B0_SendMessageTuple(0x64,  True, False),
    B0SubType.SIRENS                : B0_SendMessageTuple(0x66,  True, False),
    B0SubType.EPROM_AND_SW_VERSION  : B0_SendMessageTuple(0x69,  True, False),      # PM10 gave invalid
    B0SubType.KEEP_ALIVE            : B0_SendMessageTuple(0x6a,  True, False),
    B0SubType.SOME_LOG_75           : B0_SendMessageTuple(0x75,  True, False),
    B0SubType.IOVS                  : B0_SendMessageTuple(0x76,  True, False),
    B0SubType.TIMED_PGM_COMMAND     : B0_SendMessageTuple(0x7a,  True, False),      # for sending PGM on for timed period (secs) - 0d b0 00 7a 0b 31 80 01 ff 20 0b 04 00 01 3c 00 43 67 0a

    # 0x59 Message msgType=3 subType=89 not known about,  its chunky.   data = 03 59 0a ff 28 ff 05 01 01 16 00 00 72 43
    #             Decoded Chunk type 3   subtype 89   sequence 255  datasize 40   length 5    index MIXED            data 01 01 16 00 00
    # 0x66 Message msgType=3 subType=102 not known about, its chunky.   data = 03 66 0b ff 08 02 06 00 00 00 00 00 00 63 43
    #             Decoded Chunk type 3   subtype 102  sequence 255  datasize 8    length 6    index SIRENS           data 00 00 00 00 00 00

    B0SubType.ZONE_LUX              : B0_SendMessageTuple(0x77,  True, False),      # Zone Luminance / lux.  Tried asking for this and didn't get it on my PM10.
}

# Create a reverse lookup e.g. given the 0x0A then get the enumeration B0SubType.TAMPER_ACTIVITY
pmSendMsgB0_reverseLookup = { v.data : B0_SendMessageTuple(k, v.chunky, v.paged) for k,v in pmSendMsgB0.items() }



# Entry in a queue of commands (and PDUs) to send to the panel
class VisonicListEntry:
    """This is an entry in the queue of commands to send to the panel."""

    def __init__(self, command : VisonicCommand | None = None, raw : bytearray | None = None, options : list[tuple[int,int|bytearray]] | None = None, response : list[Receive] | None = None) -> None:
        """Initialise list entry."""
        self.command : VisonicCommand | None = command
        self.options : list | None = options
        self.raw : bytearray | None = raw
        self.response : list[Receive] = [] if response is None else response

        if command is None and raw is None:
            raise PyVisonicException("One of Command or Raw must be set and valid", code=101)

        if self.command is not None:
            if self.command.replytype is not None:
                self.response = self.command.replytype.copy()  # list of message reply needed
            # are we waiting for an acknowledge from the panel (do not send a message until we get it)
            if self.command.waitforack:
                self.response.append(Receive.ACKNOWLEDGE)  # add an acknowledge to the list

        self.triedResendingMessage = False
        self.created = get_local_time()

    def __str__(self):
        """Convert to a string to describe the command list entry."""
        if self.command is not None:
            return f"Command:{self.command.msg}    Options:{self.options}"
        if self.raw is not None:
            return f"Raw: {toString(self.raw)}"
        return "Command:None"

    def __lt__(self, other: object) -> bool:             # Implement < based on the creation time
        """Less than function for PriorityQuete."""
        if not isinstance(other, VisonicListEntry):
            raise TypeError(f"Cannot compare VisonicListEntry with {type(other)}")
        return self.created < other.created

    def insertOptions(self, data : bytearray) -> bytearray:
        """Add options to this list entry."""
        if self.options is None:
            return data
        # push in the options in to the appropriate places in the message. Examples are the pin or the specific command
        # the length of instruction.options has to be an even number
        # it is a list of couples:  bitoffset , bytearray to insert
        #op = int(len(instruction.options) / 2)
        # log.debug(f"[sendPdu] Options {instruction.options} {op}")
        for opt in self.options:
            s, a = opt   # bit offset as an integer, the int or bytearray to insert
            if isinstance(a, int):
                data[s] = a
            elif isinstance(a, bytearray):
                data[s : s + len(a)] = a
            else:
                raise NotImplementedError
        return data


# Use PriorityQueue but add a peek function to see the head of the list
class PriorityQueueWithPeek(asyncio.PriorityQueue):
    """Extend the PriorityQueue builtin to peek and find."""

    def _items(self) -> list[tuple[Any, VisonicListEntry]]:
        """Internal helper to access underlying heap queue safely."""
        return self._queue  # type: ignore[attr-defined]

    def peek_nowait(self):
        """Peek in to the priority queue."""
        if self.empty():
            raise asyncio.QueueEmpty
        return self._items()[0]                   # PriorityQueue is an ordered list so look at the head of the list

    def find(self, v: VisonicListEntry) -> tuple[Any, VisonicListEntry] | None:
        """Find in the priority queue."""
        if v.command is None:
            return None
        for item in self._items():
            _, entry = item
            if (
                isinstance(entry, VisonicListEntry)
                and v.command == entry.command
                and v.raw == entry.raw
                and v.options == entry.options
            ):
                return item
        return None

    def exists(self, v: VisonicListEntry) -> bool:
        """See if a command is already in the queue (to determine whether to add it)."""
        return self.find(v) is not None
