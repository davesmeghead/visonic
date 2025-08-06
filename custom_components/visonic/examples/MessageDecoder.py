
import os,sys,inspect,traceback
# set the parent directory on the import path
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(1000000,parentdir) 

import logging
from pyvisonic import VisonicProtocol

#    0d b0 03 24 22 ff 08 ff 1d 07 00 00 00 22 00 00 00 0a 06 0c 05 08 19 14 03 03 00 87 00 00 00 87 00 00 00 05 00 00 45 43 be 0a
d = "0d b0 03 0f 0f 07 08 0f 00 00 01 43 03 00 87 00 87 00 05 40 43 31 0a"
log = logging.getLogger()
log.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
#formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
formatter = logging.Formatter('%(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)

visonicProtocol = VisonicProtocol(panelConfig={}, panel_id=0, loop=None)
visonicProtocol.setLogger(log)
visonicProtocol.handle_msgtype_testing(bytearray.fromhex(d))
