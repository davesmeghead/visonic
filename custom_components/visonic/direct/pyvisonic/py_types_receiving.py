"""Types for rad received data."""
from dataclasses import dataclass, field
from enum import Enum, auto
import logging
from typing import Final, NamedTuple

from .py_const import B0_35_PANEL_DATA_LOG, B0_42_PANEL_DATA_LOG, OBFUS, DebugLevel
from .py_enum import DataType, IndexName, Receive
from .py_utils import b2i, toString

log = logging.getLogger(__name__)

# Debug Settings (what information to put in the log files) - Receiving Messages from the Panel
RecvDebugC = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug incoming control messages
RecvDebugM = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug incoming message data
RecvDebugD = DebugLevel.CMD if OBFUS else DebugLevel.FULL   # Debug incoming EPROM message data
RecvDebugI = DebugLevel.NONE if OBFUS else DebugLevel.FULL  # Debug incoming image data

###################################################################################
################## Messages that we can receive from the panel  ###################
###################################################################################

# Message types we can receive with their length and whether they need an ACK.
#    When isvariablelength is True:
#             the length is the fixed number of bytes in the message.  Add this to the flexiblelength when it is received to get the total packet length.
#             varlenbytepos is the byte position of the variable length of the message.
#    flexiblelength provides support for messages that have a variable length
#    checksum defines the crc checksum that needs doing on the message to validate it, IGNORE is for messages that do not have a checksum.
#    When length is 0 then we stop processing the message on the first Packet.FOOTER. This is only used for the short messages (4 or 5 bytes long) like ack, stop, denied and timeout

class ChecksumType(Enum):
    """Checksum type."""
    NORMAL = auto()
    IMAGE_DATA = auto()
    IGNORE = auto()

class PanelCallBack(NamedTuple):
    """Visonic Protocol Receiver Callback Definition."""
    length: int
    ackneeded: bool
    isvariablelength: bool
    varlenbytepos: int
    flexiblelength: int
    checksum: ChecksumType
    debugprint: DebugLevel  # Using the Enum type directly
    msg: str

# PanelCallBack = collections.namedtuple("PanelCallBack", 'length ackneeded isvariablelength varlenbytepos flexiblelength checksum debugprint msg' )
pmReceiveMsg: Final[dict[Receive, PanelCallBack | dict[int, PanelCallBack]]] = {
    Receive.DUMMY_MESSAGE      : PanelCallBack(  0,  True, False, -1, 0, ChecksumType.NORMAL, DebugLevel.NONE,                          "Dummy Message" ),       # Dummy message used in the algorithm when the message type is unknown. The -1 is used to indicate an unknown message in the algorithm
    Receive.ACKNOWLEDGE        : PanelCallBack(  0, False, False,  0, 0, ChecksumType.NORMAL, DebugLevel.NONE,                          "Acknowledge" ),         # Ack
    Receive.TIMEOUT            : PanelCallBack(  0,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugC,                          "Timeout" ),             # Timeout. See the receiver function for ACK handling
    Receive.UNKNOWN_07         : PanelCallBack(  0,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugC,                          "Unknown 07" ),          # No idea what this means but decode it anyway
    Receive.ACCESS_DENIED      : PanelCallBack(  0,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugC,                          "Access Denied" ),       # Access Denied
    Receive.LOOPBACK_TEST      : PanelCallBack(  0, False, False,  0, 0, ChecksumType.NORMAL, DebugLevel.FULL,                          "Loopback Test" ),       # THE PANEL DOES NOT SEND THIS. THIS IS USED FOR A LOOP BACK TEST
    Receive.EXIT_DOWNLOAD      : PanelCallBack(  0,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugC,                          "Exit Download" ),       # The panel may send this during download to tell us to exit download
    Receive.UNKNOWN_1F         : PanelCallBack(  0,  True, False,  0, 0, ChecksumType.NORMAL, DebugLevel.FULL,                          "Do not know what this is" ), # My Powermaster 30 sent this
    Receive.NOT_USED           : PanelCallBack( 14,  True, False,  0, 0, ChecksumType.NORMAL, DebugLevel.FULL,                          "Not Used" ),            # 14 Panel Info (older visonic powermax panels so not used by this integration)
    Receive.DOWNLOAD_RETRY     : PanelCallBack( 14,  True, False,  0, 0, ChecksumType.NORMAL, DebugLevel.CMD  if OBFUS else RecvDebugD, "Download Retry" ),      # 14 Download Retry
    Receive.DOWNLOAD_SETTINGS  : PanelCallBack( 14,  True, False,  0, 0, ChecksumType.NORMAL, DebugLevel.NONE if OBFUS else RecvDebugD, "Download Settings" ),   # 14 Download Settings
    Receive.PANEL_INFO         : PanelCallBack( 14,  True, False,  0, 0, ChecksumType.NORMAL, DebugLevel.FULL,                          "Panel Info" ),          # 14 Panel Info
    Receive.DOWNLOAD_BLOCK     : PanelCallBack(  7,  True,  True,  4, 5, ChecksumType.NORMAL, DebugLevel.CMD  if OBFUS else RecvDebugD, "Download Block" ),      # Download Info in varying lengths  (For variable length, the length is the fixed number of bytes). This contains panel data so don't log it.
    Receive.EVENT_LOG          : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugM,                          "Event Log (A0)" ),      # 15 Event Log
    Receive.ZONE_NAMES         : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugM,                          "Zone Names (A3)" ),     # 15 Zone Names
    Receive.STATUS_UPDATE      : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugM,                          "Status Update (A5)" ),  # 15 Status Update       Length was 15 but panel seems to send different lengths
    Receive.ZONE_TYPES         : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugM,                          "Zone types (A6)" ),     # 15 Zone Types
    Receive.PANEL_STATUS       : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugM,                          "Panel Status (A7)" ),   # 15 Panel Status Change
    Receive.POWERLINK          : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugC,                          "Powerlink (AB)" ),      # 15 Enrol Request 0x0A  OR Ping 0x03      Length was 15 but panel seems to send different lengths
    Receive.SWITCH_NAMES       : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL,      RecvDebugC,                          "Switch Names" ),        # 15 Switch Names
    Receive.IMAGE_MGMT         : PanelCallBack( 15,  True, False,  0, 0, ChecksumType.NORMAL, DebugLevel.CMD  if OBFUS else RecvDebugI, "JPG Mgmt" ),            # 15 Panel responds with this when we ask for JPG images
    Receive.POWERMASTER        : PanelCallBack(  8,  True,  True,  4, 2, ChecksumType.NORMAL, DebugLevel.CMD  if OBFUS else RecvDebugM, "PowerMaster (B0)" ),    # The B0 message comes in varying lengths, sometimes it is shorter than what it states and the CRC is sometimes wrong
    Receive.REDIRECT           : PanelCallBack(  5, False,  True,  2, 0, ChecksumType.NORMAL, DebugLevel.FULL,                          "Redirect" ),            # TESTING: These are redirected Powerlink messages. 0D C0 len <data> cs 0A   so 5 plus the original data length
    Receive.PROXY              : PanelCallBack( 11,  True, False,  0, 0, ChecksumType.NORMAL, DebugLevel.FULL,                          "Proxy" ),               # VISPROX : Interaction with Visonic Proxy
    Receive.PROXY_COMMAND      : PanelCallBack(  7, False, False,  0, 0, ChecksumType.NORMAL, DebugLevel.FULL,                          "Proxy Cmd Ringback"),   # VISPROX : Interaction with Visonic Proxy, this is a command that has ringback so something is wrong
    # The F1 message needs to be ignored, I have no idea what it is but the crc is always wrong and only Powermax+ panels seem to send it. Assume a minimum length of 9, a variable length and ignore the checksum calculation.
    Receive.UNKNOWN_F1         : PanelCallBack(  9,  True,  True,  0, 0, ChecksumType.IGNORE,      RecvDebugC,                          "Unknown F1" ),          # Ignore checksum on all F1 messages
    # The F4 message comes in varying lengths. It is the image data from a PIR camera. The image path (01/03/05) is
    # NOT checksum gated: this panel emits valid frames carrying a CRC that doesn't match the bytes, so gating drops
    # good data. A genuinely corrupt image is caught downstream when it fails to decode. 15 is the handshake, so it
    # is still checked. Verified against a real Powerlink on the wire, see PR #255.
    Receive.IMAGE_DATA : {
        0x01 : PanelCallBack(  9, False, False,  0, 0, ChecksumType.IGNORE    , RecvDebugI, "Panel Ack" ),            # not an image footer - a constant 0d f4 01 00 00 00 e4 c0 0a the panel sends after F4-10/F4-05/AB/F4-03 alike
        0x03 : PanelCallBack(  9, False,  True,  5, 0, ChecksumType.IGNORE    , RecvDebugI, "Image Header" ),        # 32/33 passed on a clean-wire capture; the one failure was on an otherwise good image
        0x05 : PanelCallBack(  9, False,  True,  5, 0, ChecksumType.IGNORE    , RecvDebugI, "Image Data" ),          # only 309/699 passed; gating drops chunks incl the JPEG SOF/SOS markers
        0x15 : PanelCallBack( 13, False, False,  0, 0, ChecksumType.IMAGE_DATA, RecvDebugI, "Image Keep-Alive" )     # handshake, not image payload - validates cleanly
    }
}

###################################################################################
##########################  B0 Data to Retrieve ###################################
###################################################################################
class PanelSettingsCollection(NamedTuple):
    """Visonic Panel Settings Definition for B0_35 messages."""
    sequence: list | None
    length: int
    processinstandard: bool
    display: bool
    datatype: DataType  # Assuming DataType is an Enum
    datacount: int
    msg: str

# PanelSettingsCollection = collections.namedtuple('PanelSettingsCollection', 'sequence length processinstandard display datatype datacount msg') # overall length in bytes, datatype in bits
pmPanelSettingsB0_35: Final[dict[int, PanelSettingsCollection]] = {
    0x0000 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         6, "Central Station Account Number 1"),  # size of each entry is 6 nibbles
    0x0001 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         6, "Central Station Account Number 2"),  # size of each entry is 6 nibbles
    0x0002 : PanelSettingsCollection(      None,   0,  True, B0_35_PANEL_DATA_LOG,                DataType.FF_PADDED_STRING,          0, "Panel Serial Number"),
    0x0003 : PanelSettingsCollection(      None,   9,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,        12, "Central Station IP 1"),              # 12 nibbles e.g. 192.168.010.001
    0x0004 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         0, "Central Station Port 1"),
    0x0005 : PanelSettingsCollection(      None,   9,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,        12, "Central Station IP 2"),              # 12 nibbles
    0x0006 : PanelSettingsCollection(      None,   6,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         0, "Central Station Port 2"),
    0x0007 : PanelSettingsCollection(      None,  39,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Capabilities unknown"),
    0x0008 : PanelSettingsCollection(      None,  99, False, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         2, "User Code"),                         # size of each entry is 4 nibbles
    0x000D : PanelSettingsCollection( [1,2,255],   0,  True, B0_35_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 32, "Zone Names"),                        # 32 nibbles i.e. each string name is 16 bytes long. The 0x35 message has 3 sequenced messages.
    0x000F : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         4, "Download Code"),                     # size of each entry is 4 nibbles
    0x0010 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Panel EPROM Version 1"),
 #  0x0011 SMS_MMS_BY_SERVER_TEL1
 #  0x0012 SMS_MMS_BY_SERVER_TEL2
 #  0x0013 SMS_MMS_BY_SERVER_TEL3
 #  0x0014 SMS_MMS_BY_SERVER_TEL4
 #  0x0015 EMAIL_BY_SERVER_EMAIL1
 #  0x0016 EMAIL_BY_SERVER_EMAIL2
 #  0x0017 EMAIL_BY_SERVER_EMAIL3
 #  0x0018 EMAIL_BY_SERVER_EMAIL4
 #  0x0019 SOME_SETTINGS25
    0x0024 : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Panel EPROM Version 2"),
 #  0x0027 TYPE_OFFSETS - no idea what this means!
 #  0x0028 CAPABILITIES
    0x0029 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   0, "Unknown B"),
 #  0x002B UNKNOWN_SOFTWARE_VERSION
    0x002C : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel Default Version"),
    0x002D : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel Software Version"),
    0x0030 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                   0, "Partition Enabled"),
 #  0x0031 ASSIGNED_ZONE_TYPES
 #  0x0032 ASSIGNED_ZONE_NAMES
    0x0033 : PanelSettingsCollection(      None,  67,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                  64, "Zone Chime Data"),
 #  0x0034 MAP_VALUE
 #  0x0035 MAP_VALUE_2
    0x0036 : PanelSettingsCollection(      None,  67,  True, B0_35_PANEL_DATA_LOG,                DataType.INTEGER,                  64, "Partition Data"),
 #  0x0037 TAG_PARTITION_ASSIGNMENT
 #  0x0038 KEYPAD_PARTITION_ASSIGNMENT
 #  0x0039 SIREN_PARTITION_ASSIGNMENT
    0x003C : PanelSettingsCollection(      None,   0,  True, B0_35_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING,       0, "Panel Hardware Version"),
    0x003D : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel RSU Version"),
    0x003E : PanelSettingsCollection(      None,  19,  True, B0_35_PANEL_DATA_LOG,                DataType.STRING,                    0, "Panel Boot Version"),
    0x0042 : PanelSettingsCollection( [1,2,255],   0,  True, B0_35_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 32, "Custom Zone Names"),
 #  0x0045 ZONE_NAMES2
 #  0x0046 CUSTOM_ZONE_NAMES2
 #  0x0047 H24_TIME_FORMAT
 #  0x0048 US_DATE_FORMAT
 #  0x004D PRIVATE_REPORTING_TELNOS
 #  0x004E MAX_PARTITIONS - NEEDS CHECKING SHOWS 03
 #  0x0051 SMS_REPORT_NUMBERS
    0x0054 : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         4, "Installer Code"),                    # size of each entry is 4 nibbles
    0x0055 : PanelSettingsCollection(      None,   5,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         4, "Master Code"),                       # size of each entry is 4 nibbles
 #  0x0056 GUARD_CODE
 #  0x0057 EN50131_EXIT_DELAYS
    0x0058 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   2, "Unknown D"),                         # 0x0058 EXIT_DELAY maybe
    0x0106 : PanelSettingsCollection(      None,   4,  True, B0_35_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   1, "Unknown A"),                         # size of each entry is 4 nibbles
}

pmPanelSettingsB0_42 = {
    0x0000 : PanelSettingsCollection(      None,  20,  True, B0_42_PANEL_DATA_LOG,                DataType.DIRECT_MAP_STRING,         3, "Central Station Account Number 1"),  # size of each entry is 6 nibbles
    0x0002 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.FF_PADDED_STRING,          0, "Panel Serial Number"),
    0x0008 : PanelSettingsCollection(      None,   0, False, B0_42_PANEL_DATA_LOG and not OBFUS,  DataType.DIRECT_MAP_STRING,         2, "User Code"),                         # size of each entry is 4 nibbles
    0x000D : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 30, "Zone Names"),                        # 32 nibbles i.e. each string name is 16 bytes long. The 0x35 message has 3 sequenced messages.
    0x0030 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   1, "Partition Enabled"),
    0x0033 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   1, "Zone Chime Data"),
    0x0036 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   1, "Partition Data"),
    0x003C : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING,       0, "Panel Hardware Version"),
    0x0042 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.SPACE_PADDED_STRING_LIST, 32, "Custom Zone Names"),                 # variable length with the sequence of messages
#   0x00a4 : PanelSettingsCollection(     None,   0,  True, B0_42_PANEL_DATA_LOG,                DataType.INTEGER,                   2, "XXXXXXXXX"),
    0x0106 : PanelSettingsCollection(      None,   0,  True, B0_42_PANEL_DATA_LOG and not OBFUS,  DataType.INTEGER,                   1, "Unknown A"),                         # size of each entry is 4 nibbles
}


@dataclass(slots=True, eq=False)
class Chunky:
    """This is a Chunk, most B0 messages are 1 or more chunks."""
    type: int = 0
    subtype: int = 0
    sequence: int = 0
    datasize: int = 0
    index: int = 0
    length: int = 0
    data: bytearray = field(default_factory=bytearray)

    def __repr__(self):
        """Generate string that describes the chunk."""
        return self.__str__()

    def __str__(self):
        """Generate string that describes the chunk."""
        # Assume logging of all chunky data is ok unless disabled by the 0x42 or 0x35 setting
        #     Normal data does not include user codes etc, just panel and sensor status
        show_data = True
        if self.subtype == 0x42 and len(self.data) > 2:
            data_content = b2i(self.data[0:2], big_endian=False)
            if data_content in pmPanelSettingsB0_42:
                show_data = pmPanelSettingsB0_42[data_content].display
        elif self.subtype == 0x35 and len(self.data) > 2:
            data_content = b2i(self.data[0:2], big_endian=False)
            if data_content in pmPanelSettingsB0_35:
                show_data = pmPanelSettingsB0_35[data_content].display
        index_name = IndexName(self.index).name
        if show_data:
            return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  length {self.length:<3}  index {index_name:<14}   data {toString(self.data)}"
        return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  length {self.length:<3}  index {index_name:<14}   obfus datalen = {len(self.data)}"

    def GetItAll(self):
        """Return a raw string that describes the chunk."""
        # Get it all, ignore display setting and obfus
        index_name = IndexName(self.index).name
        return f"type {self.type:<2}  subtype {self.subtype:<3}  sequence {self.sequence:<3}  datasize {self.datasize:<3}  length {self.length:<3}  index {index_name:<14}   data {toString(self.data)}"
