
import os,sys,inspect,traceback
# set the parent directory on the import path
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(1000000,parentdir) 

import logging
from pyvisonic import VisonicProtocol

#    0d b0 03 24 22 ff 08 ff 1d 07 00 00 00 22 00 00 00 0a 06 0c 05 08 19 14 03 03 00 87 00 00 00 87 00 00 00 05 00 00 45 43 be 0a
#d = "0d 1f 43 d4 0a 0d 02 43 ba 0a"
#d = "0d b0 03 24 22 ff 08 ff 1d 0f 00 00 00 00 00 00 00 0a 2d 0c 09 08 19 14 07 03 00 81 00 00 00 81 00 00 00 01 00 00 01 43 fd 0a"
d = "0d b0 03 38 11 ff 20 ff 0c 01 00 00 00 02 00 03 00 05 00 00 00 2d 43 5b 0a"
d = "0d a5 00 04 00 61 0c 05 00 04 00 00 43 9c 0a"
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
