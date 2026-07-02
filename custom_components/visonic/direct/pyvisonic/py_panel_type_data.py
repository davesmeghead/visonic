"""Panel Type data."""

from .py_enum import CFG

###################################################################################
##########################  Panel Type Information  ###############################
###################################################################################

#################################################################
######### Known Panel Types to work (or not) ####################
#    PanelType=0 : PowerMax , Model=21   Powermaster False  <<== THIS DOES NOT WORK (NO POWERLINK SUPPORT and only supports EPROM download i.e no sensor data) ==>>
#    PanelType=1 : PowerMax+ , Model=33   Powermaster False
#    PanelType=1 : PowerMax+ , Model=47   Powermaster False
#    PanelType=2 : PowerMax Pro , Model=22   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=17   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=62   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=71   Powermaster False
#    PanelType=4 : PowerMax Pro Part , Model=86   Powermaster False
#    PanelType=5 : PowerMax Complete Part , Model=18   Powermaster False
#    PanelType=5 : PowerMax Complete Part , Model=79   Powermaster False
#    PanelType=7 : PowerMaster10 , Model=32   Powermaster True
#    PanelType=7 : PowerMaster10 , Model=68   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    PanelType=7 : PowerMaster10 , Model=153   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=6   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=53   Powermaster True
#    PanelType=8 : PowerMaster30 , Model=63   Powermaster True   #  This is my test panel, all 0x3F  Message data is formatted correctly
#    PanelType=10: PowerMaster33 , Model=71   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    PanelType=15: PowerMaster33 , Model=146   Powermaster True  #  Under investigation.
#################################################################


# Panel Names for each panel type (0-16).
#     0 : "PowerMax" is not a supported panel type
#     Assume 360R is Panel 16 for this release as it was released after the PM33, also I've an old log file from a user that indicates this
pmPanelType = {
    0 : "PowerMax",
    1 : "PowerMax+",
    2 : "PowerMax Pro",
    3 : "PowerMax Complete",
    4 : "PowerMax Pro Part",
    5 : "PowerMax Complete Part",
    6 : "PowerMax Express",
    7 : "PowerMaster 10",
    8 : "PowerMaster 30",
    10 : "PowerMaster 33",
    13 : "PowerMaster 360",
    15 : "PowerMaster 33",
    16 : "PowerMaster 360R",
    17 : "Default"                     # This is the default panel settings i.e. the most basic panel
}

# Config for each panel type (0-16).
#     Assume 360R is Panel 16 for this release as it was released after the PM33, also I've an old log file from a user that indicates this
#               So make column 16 the same as column 13
#     Don't know what 9, 11, 12 or 14 are so just copy other settings. I know that there are Commercial/Industry Panel versions so it might be them
#     This data defines each panel type's maximum capability
#     I know that panel types 4 and 5 support 3 partitions but I can't figure out how they are represented in A5 and A7 messages, so partitions only supported for PowerMaster and B0 messages

pmPanelConfig = {       #     0       1       2       3       4       5       6       7       8       9      10      11      12      13      14      15      16      17    See pmPanelType above
    CFG.SUPPORTED      : ( False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True ), # Supported Panels i.e. not a PowerMax
    CFG.KEEPALIVE      : (     0,     25,     25,     25,     25,     25,     25,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15 ), # Keep Alive message interval
    CFG.DLCODE_1       : (    "", "5650", "5650", "5650", "5650", "5650", "5650", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "5650" ), # Default download codes (for reset panels or panels that have not been changed)
    CFG.DLCODE_2       : (    "", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "AAAA", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "5650", "AAAA" ), # Alternative 1 (Master) known default download codes
    CFG.DLCODE_3       : (    "", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB", "BBBB" ), # Alternative 2 (Master) known default download codes
    CFG.PARTITIONS     : (     0,      1,      1,      1,      1,      1,      1,      3,      3,      3,      3,      3,      3,      3,      3,      3,      3,      1 ), # Force all PowerMax Panels to only have 1 partition
    CFG.EVENTS         : (     0,    250,    250,    250,    250,    250,    250,    250,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000,    250 ),
    CFG.KEYFOBS        : (     0,      8,      8,      8,      8,      8,      8,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      8 ),
    CFG.ONE_WKEYPADS   : (     0,      8,      8,      8,      8,      8,      8,      0,      0,      0,      0,      0,      0,      0,      0,      0,      0,      8 ),
    CFG.TWO_WKEYPADS   : (     0,      2,      2,      2,      2,      2,      2,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      2 ),
    CFG.SIRENS         : (     0,      2,      2,      2,      2,      2,      2,      4,      8,      8,      8,      8,      8,      8,      8,      8,      8,      2 ),
    CFG.USERCODES      : (     0,      8,      8,      8,      8,      8,      8,      8,     48,     48,     48,     48,     48,     48,     48,     48,     48,      8 ),
    CFG.REPEATERS      : (     0,      0,      0,      0,      0,      0,      0,      4,      8,      4,      4,      4,      4,      4,      4,      4,      4,      0 ),
    CFG.PROXTAGS       : (     0,      0,      8,      0,      8,      8,      0,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      0 ),
    CFG.ZONECUSTOM     : (     0,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5 ),
    CFG.DEV_ZONE_TYPES : (     0,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30 ),
    CFG.WIRELESS       : (     0,     28,     28,     28,     28,     28,     29,     29,     62,     62,     62,     62,     62,     64,     62,     62,     64,     28 ), # Wireless + Wired total 30 or 64
    CFG.WIRED          : (     0,      2,      2,      2,      2,      2,      1,      1,      2,      2,      2,      2,      2,      0,      2,      2,      0,      2 ),
    CFG.SWITCH         : (     0,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15 ), # Supported switch devices
    CFG.PGM            : (     0,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1 ), # PGM
    CFG.AUTO_ENROL     : (  None,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False,  False ), # 360 and 360R cannot autoenrol to Powerlink
    CFG.AUTO_SYNCTIME  : (  None,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False ), # Assume 360 and 360R can auto sync time
    CFG.POWERMASTER    : (  None,  False,  False,  False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False ), # Panels that use and respond to the additional PowerMaster Messages
    CFG.EPROM_DOWNLOAD : (  None,   True,   True,   True,   True,   True,   True,   True,  False,  False,  False,  False,  False,  False,  False,  False,  False,   True ), # Panel does EPROM Download (True) or can do B0 Message Download (False)
    CFG.AB_SUPPORTED   : ( False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False,  False ), # Are AB command messages supported (without a bridge)
    CFG.INIT_SUPPORT   : (  None,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False,  False )  # Panels that support the INIT command
}


