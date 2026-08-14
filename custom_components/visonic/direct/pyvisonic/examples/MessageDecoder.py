"""Simple message decoder."""

import asyncio
import logging
from pathlib import Path
import sys

package_dir = Path(__file__).resolve().parent.parent
project_dir = package_dir.parent
sys.path.insert(0, str(project_dir))
print(sys.path[0])
from pyvisonic.py_visonic import VisonicProtocol


#    0d b0 03 24 22 ff 08 ff 1d 07 00 00 00 22 00 00 00 0a 06 0c 05 08 19 14 03 03 00 87 00 00 00 87 00 00 00 05 00 00 45 43 be 0a
#d = "0d 1f 43 d4 0a 0d 02 43 ba 0a"
#d = "0d b0 03 24 22 ff 08 ff 1d 0f 00 00 00 00 00 00 00 0a 2d 0c 09 08 19 14 07 03 00 81 00 00 00 81 00 00 00 01 00 00 01 43 fd 0a"
d = "0d b0 03 38 11 ff 20 ff 0c 01 00 00 00 02 00 03 00 05 00 00 00 2d 43 5b 0a"
d = "0d b0 03 0f 0b 19 08 0f 00 11 01 01 01 07 83 82 43 9d 0a"

log = logging.getLogger()
log.setLevel(logging.DEBUG)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
#formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
formatter = logging.Formatter('%(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)


loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

#     def __init__(self, loop, force_standard_mode : bool, disable_all_commands : bool, download_code : str, user_code_slot: int, logger = None) -> None:


visonicProtocol = VisonicProtocol(loop=loop, force_standard_mode=False, disable_all_commands=False, download_code="AAAA", user_code_slot=0, logger=log)
#visonicProtocol.setLogger(log)
visonicProtocol.handle_msgtype_testing(bytearray.fromhex(d))
