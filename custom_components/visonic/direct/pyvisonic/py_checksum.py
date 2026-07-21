"""Checksum calculator and validator."""

import logging

from .py_types_receiving import ChecksumType

log = logging.getLogger(__name__)

class MyChecksumCalc:
    """Checksum Calculation Class."""

    def __init__(self, logger = None) -> None:
        """Initialize class."""
        self.log = logger

    # This is used for debugging from command line
    def setLogger(self, loggy):
        """Set the logger."""
        self.log = loggy

    # check the checksum of received messages
    def _validatePDU(self, checksum_type: ChecksumType, packet: bytearray) -> bool:
        r"""Verify if packet is valid. Packets start with a preamble (\x0D) and end with postamble (\x0A)."""
        # Does it start with a preamble
        if packet[:1] != b"\x0D":
            return False
        # Does it end with a footer
        if packet[-1:] != b"\x0A":
            return False

        if checksum_type == ChecksumType.IGNORE:
            # For ignore all we do is a basic header and footer check
            return True

        # F4 PIR-image messages carry a 2-byte CRC-16, not the 1-byte panel checksum
        if checksum_type == ChecksumType.IMAGE_DATA:
            if len(packet) > 3:
                return self.f4_checksum(packet[1:-3]) == (packet[-3], packet[-2])
            return False

        # fall through to do ChecksumType.NORMAL

        # Check the CRC
        if packet[-2:-1] == self._calculateCRC(packet[1:-2]):
            # log.debug("[_validatePDU] VALID CRC PACKET!")
            return True

        # Check the CRC
        if packet[-2:-1] == self._calculateCRCAlt(packet[1:-2]):
            # log.debug("[_validatePDU] VALID ALT CRC PACKET!")
            return True

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] + 1:
            #log.debug(f"[_validatePDU] Validated a Packet with a checksum that is 1 more than the actual checksum!!!! {toString(packet)} and {hex(self._calculateCRC(packet[1:-2])[0]).upper()} alt calc is {hex(self._calculateCRCAlt(packet[1:-2])[0]).upper()}")
            return True

        if packet[-2:-1][0] == self._calculateCRC(packet[1:-2])[0] - 1:
            #log.debug(f"[_validatePDU] Validated a Packet with a checksum that is 1 less than the actual checksum!!!! {toString(packet)} and {hex(self._calculateCRC(packet[1:-2])[0]).upper()} alt calc is {hex(self._calculateCRCAlt(packet[1:-2])[0]).upper()}")
            return True

        #log.debug("[_validatePDU] Not valid packet, CRC failed, may be ongoing and not final 0A")
        return False

    # alternative to calculate the checksum for sending and receiving messages
    def _calculateCRCAlt(self, msg: bytearray):
        """Calculate CRC Checksum."""

        # log.debug("[_calculateCRC] Calculating for: %s", toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0: len(msg)]:
            checksum += char
        # 29/8/2022
        #      This works for both my panels and always validates exactly (never using the +1 or -1 code in _validatePDU)
        #      It also matches the checksums that the Powerlink 3.1 module generates.
        checksum = 256 - (checksum % 255)
        if checksum == 256:
            checksum = 1
        # log.debug("[_calculateCRC] Calculating for: {toString(msg)}     calculated CRC is: {toString(bytearray([checksum]))}")
        return bytearray([checksum])

    # calculate the checksum for sending and receiving messages
    def _calculateCRC(self, msg: bytearray):
        """Calculate CRC Checksum."""
        # log.debug("[_calculateCRC] Calculating for: %s", toString(msg))
        # Calculate the checksum
        checksum = 0
        for char in msg[0: len(msg)]:
            checksum += char
        checksum = 0xFF - (checksum % 0xFF)
        if checksum == 0xFF:
            checksum = 0x00
        # log.debug("[_calculateCRC] Calculating for: {toString(msg)}     calculated CRC is: {toString(bytearray([checksum]))}")
        return bytearray([checksum])

    def f4_checksum(self, body: bytearray) -> tuple[int, int]:
        """CRC-16/CCITT (poly 0x1021, init 0, no xorout) for F4 messages, output byte-swapped.

        Both F4-07 and F4-10 acks use this unchanged - verified byte-for-byte against a real
        Powerlink on the wire (104/105 captured acks). The earlier 0xE700 xorout on F4-07 was wrong.
        """
        crc = 0
        for by in body:
            crc ^= by << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return crc & 0xFF, (crc >> 8) & 0xFF
