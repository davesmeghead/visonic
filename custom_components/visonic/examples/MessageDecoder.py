
import os,sys,inspect,traceback
# set the parent directory on the import path
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(1000000,parentdir) 

import logging
from pyvisonic import VisonicProtocol


d = "0d b0 03 42 23 ff 08 ff 1e 1e 01 02 00 80 00 00 00 05 10 00 00 01 00 04 7e 7e 4f 18 80 0a 24 00 c8 20 00 00 00 00 00 d5 43 ef 0a"
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
