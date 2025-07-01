
import os,sys,inspect,traceback
# set the parent directory on the import path
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(1000000,parentdir) 

import logging
from pyvisonic import VisonicProtocol


#d = "02 1f 94 ff 08 00 04 00 00 00 00 ff 08 01 0f 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ff 08 02 08 01 00 00 00 00 00 00 00 ff 08 03 40 04 04 2c 0c 0c 2c 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ff 08 04 20 06 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 08 05 00 13 43"
#d = "03 1d 35 ff 01 00 01 00 ff 01 01 02 00 00 ff 01 02 01 00 ff 01 03 08 22 00 00 00 00 00 42 00 ff 01 04 04 00 00 00 00 ff 01 05 04 00 00 00 00 ff 01 0a 04 00 00 00 00 f4 43"
d = "03 0f 0f 07 08 0f 00 00 00 43 03 00 87 00 87 00 07 24 43"
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
visonicProtocol.handle_msgtypeB0(bytearray.fromhex(d))
