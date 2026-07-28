#!/usr/bin/env python3

#############################################################
# Copyright 2026 Lawrence Livermore National Security, LLC
# (c.f. AUTHORS, NOTICE.LLNS, COPYING)
#
# This file is part of the Flux resource manager framework.
# For details, see https://github.com/flux-framework.
#
# SPDX-License-Identifier: LGPL-3.0
#############################################################

"""
Avery Berkel 6702 bench scale driver (ECR mode).

Extracted from the original brewcop.py so the serial protocol lives in one
importable place, shared by the touchscreen app and the bring-up probe.
The wire protocol (9600 7E1, W/Z commands, response layouts, status codes)
is documented in docs/scale-hardware.md and is unchanged from the original
implementation -- only the serial port path is now configurable, and a
structured poll() result is exposed for logging/probing.

pyserial (python3-serial) is imported lazily so this module can be imported
on machines without it (e.g. for unit-testing the pure parsing logic, or on
a dev box); only constructing a real Scale() requires it.
"""

# Default serial device.  The original Pi 2 build used the GPIO UART at
# /dev/ttyAMA0; the modernized unit uses a USB RS-232 adapter, so callers
# will typically pass a /dev/serial/by-id/... path or "auto".
DEFAULT_SERIAL_PATH = "/dev/ttyAMA0"

# Pounds -> grams (ECR weigh response reports pounds).
LB_TO_G = 453.592

# Empty-carafe / pot tare tolerance: readings within this many grams of the
# tare are treated as "at tare" (see docs and MODERNIZATION.md).
POT_TOLERANCE_G = 4


class ScaleError(Exception):
    """Raised on protocol/framing errors talking to the scale."""


class Scale:
    """
    Manage the Avery-Berkel 6702-16658 bench scale in ECR mode.

    Serial framing: 9600 baud, 7 data bits, even parity, 1 stop bit.
    """

    def __init__(self, path=DEFAULT_SERIAL_PATH):
        import serial  # lazy: only needed for a real scale

        self.path_serial = path
        self._weight = 0.0
        self._weight_is_valid = False
        self.ecr_status = None
        self.tare_offset = 0.0

        self.ser = serial.Serial()
        self.ser.port = path
        self.ser.baudrate = 9600
        self.ser.timeout = 0.25
        self.ser.parity = serial.PARITY_EVEN
        self.ser.bytesize = serial.SEVENBITS
        self.ser.stopbits = serial.STOPBITS_ONE
        self.ser.xonxoff = False
        self.ser.rtscts = False
        self.ser.dsrdtr = False
        self.ser.open()

    def ecr_set_status(self, response):
        """Parse response and set internal ECR status"""
        assert len(response) == 6
        assert response[0:2] == b"\nS"
        assert response[4:5] == b"\r"
        self.ecr_status = response[2:4]

    def ecr_read(self):
        """Read to ECR EOT (3)"""
        message = bytearray()
        while len(message) == 0 or message[-1] != 3:
            ch = self.ser.read(size=1)
            if len(ch) != 1:
                raise ScaleError("serial read timeout")
            message.append(ch[0])
        return message

    def zero(self):
        """Send ECR Zero command to the scale and read back status"""
        self.ser.reset_input_buffer()
        self.ser.write(b"Z\r")
        response = self.ecr_read()
        self.ecr_set_status(response)

    def poll(self):
        """
        Send ECR Weigh command to the scale and read back either
        weight + status, or just status.  If a valid weight is returned,
        set _weight_is_valid True and convert pounds to grams.

        Returns the raw response bytearray (useful for logging/probing).
        """
        self.ser.reset_input_buffer()
        self.ser.write(b"W\r")
        response = self.ecr_read()
        if len(response) == 16:
            assert response[0:1] == b"\n"
            assert response[7:10] == b"LB\r"
            self._weight = float(response[1:7]) * LB_TO_G
            self.ecr_set_status(response[10:16])
            self._weight_is_valid = True
        else:
            self.ecr_set_status(response)
            self._weight_is_valid = False
        return response

    def tare(self):
        """Incorporate weight of container on scale into future measurements"""
        self.tare_offset = self._weight

    @property
    def at_zero(self):
        """
        Test if scale status indicates scale is at zero.  The zero LED
        on the scale will be lit in this case.
        """
        if self.ecr_status == b"20":
            return True
        return False

    @property
    def status_text(self):
        """
        Human-readable interpretation of the current ECR status, independent
        of any UI toolkit.  (The original Scale.display returned urwid color
        tuples; keep presentation out of the driver.)
        """
        if self._weight_is_valid:
            return "valid"
        elif self.ecr_status in (b"10", b"30"):
            return "moving"
        elif self.ecr_status in (b"01", b"11"):
            return "under"
        elif self.ecr_status == b"02":
            return "over"
        elif self.ecr_status is None:
            return "no-status"
        else:
            return "status:" + self.ecr_status.decode("utf-8", "replace")

    @property
    def weight_is_valid(self):
        """Return True if most recent poll() returned a valid weight."""
        return self._weight_is_valid

    @property
    def weight(self):
        """Return most recently measured weight (grams), less tare offset."""
        return self._weight - self.tare_offset


class NoScale(Scale):
    """
    Dummy version of the scale for UI testing without hardware.
    """

    def __init__(self):
        # Intentionally do not call Scale.__init__ (no serial import/open).
        self._weight = 0.0
        self._weight_is_valid = True
        self.ecr_status = None
        self.tare_offset = 0.0
        self.path_serial = None

    def poll(self):
        return b""

    def zero(self):
        return

    @property
    def status_text(self):
        return "no-scale"


def open_scale(path=DEFAULT_SERIAL_PATH):
    """
    Try to open a real Scale at `path` (or auto-detect if path == "auto");
    fall back to NoScale if that fails.  Returns (scale, error_or_None).
    """
    try:
        if path == "auto":
            path = autodetect_port()
            if path is None:
                return NoScale(), "no serial device found (auto)"
        return Scale(path), None
    except Exception as e:  # serial missing, port absent, open failed
        return NoScale(), str(e)


def autodetect_port():
    """
    Best-effort guess at the USB serial device: prefer a stable
    /dev/serial/by-id/ symlink, else the first /dev/ttyUSB*.  Returns a path
    string or None.  (Kept dependency-free so it works without pyserial.)
    """
    import glob

    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    if by_id:
        return by_id[0]
    tty = sorted(glob.glob("/dev/ttyUSB*"))
    if tty:
        return tty[0]
    return None


# vim: tabstop=4 shiftwidth=4 expandtab
