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
Phidgets i-Snail AC current-sensor driver.

Brew detection senses the Moccamaster boiler current (idle ~0.5 A, a
sustained ~12.5 A burst while brewing).  The i-Snail VC-25 does the AC RMS
in hardware and outputs 0-5 V DC proportional to current (25 A full scale);
it plugs into a VINT hub that presents USB to the Pi.  We read that hub's
VoltageInput channel and convert to amps -- callers never see volts, because
volts is just this transducer's encoding, not the quantity we care about.

Shaped exactly like scale.py so it drops into brewcop the same way: a pure
conversion function, a real sensor class, a NoOp fallback for
dev-boxes/hardware-absent, and an open_* helper returning (sensor, error).

The Phidgets Python side is brewcop's own phidget22_min ctypes binding over
the apt-installable libphidget22 C library, imported lazily (like scale.py
lazily imports pyserial) so this module imports cleanly with neither the
library nor the hardware present.
"""

# i-Snail VC-25: 25 A full scale mapped linearly onto a 0-5 V output.
ISNAIL_FULL_SCALE_A = 25.0
ISNAIL_FULL_SCALE_V = 5.0

# Brew/idle boundary in amps.  A live brew measured ~12.5 A against ~0.5 A
# idle, so ~5 A (the old 1.0 V probe threshold) sits between with wide margin.
DEFAULT_THRESHOLD_A = 5.0

# How long openWaitForAttachment blocks before giving up.  One-time cost at
# startup; if the hub is absent we fall back to NoCurrentSensor after this.
ATTACH_TIMEOUT_MS = 5000


def amps_from_volts(volts):
    """Convert the i-Snail's 0-5 V output to RMS amps (pure; no hardware)."""
    return volts * (ISNAIL_FULL_SCALE_A / ISNAIL_FULL_SCALE_V)


class CurrentSensor:
    """
    A Phidgets i-Snail read through a VINT hub's VoltageInput channel.

    Configure the address (serial / hub port / channel) and attach in
    __init__, then read .amps live each tick.  .brewing applies the
    threshold.  Mirrors scale.Scale: construction does the I/O setup and
    raises on failure; open_current_sensor() wraps it with a NoOp fallback.
    """

    def __init__(
        self,
        serial=None,
        hub_port=0,
        channel=0,
        is_hubport_device=True,
        threshold_a=DEFAULT_THRESHOLD_A,
    ):
        # Lazy import: only a real sensor needs the binding / C library.
        from .phidget22_min import VoltageInput

        self.threshold_a = threshold_a
        self._vin = VoltageInput()
        if serial is not None:
            self._vin.setDeviceSerialNumber(serial)
        if hub_port is not None:
            self._vin.setHubPort(hub_port)
        # A bare 0-5 V sensor wired into a VINT port is read as a "hub port
        # device" (the port itself in voltage mode) -- our wiring.
        self._vin.setIsHubPortDevice(is_hubport_device)
        self._vin.setChannel(channel)
        self._vin.openWaitForAttachment(ATTACH_TIMEOUT_MS)

    @property
    def amps(self):
        """Live RMS current in amps (reads the channel each access)."""
        return amps_from_volts(self._vin.getVoltage())

    @property
    def brewing(self):
        """True while current is above the brew/idle threshold."""
        return self.amps >= self.threshold_a

    def close(self):
        if self._vin is not None:
            try:
                self._vin.close()
            except Exception:
                pass
            self._vin = None


class NoCurrentSensor:
    """
    Dummy sensor for dev boxes / hardware-absent runs (mirrors NoScale).
    Reads as None so the UI shows a blank current readout rather than a
    fabricated value, and brewing is never asserted.
    """

    def __init__(self, threshold_a=DEFAULT_THRESHOLD_A):
        self.threshold_a = threshold_a

    @property
    def amps(self):
        return None

    @property
    def brewing(self):
        return False

    def close(self):
        return


def open_current_sensor(
    serial=None,
    hub_port=0,
    channel=0,
    is_hubport_device=True,
    threshold_a=DEFAULT_THRESHOLD_A,
):
    """
    Try to open a real CurrentSensor; fall back to NoCurrentSensor if the
    binding/library is missing or no device attaches.  Returns
    (sensor, error_or_None), mirroring scale.open_scale.
    """
    try:
        return (
            CurrentSensor(
                serial=serial,
                hub_port=hub_port,
                channel=channel,
                is_hubport_device=is_hubport_device,
                threshold_a=threshold_a,
            ),
            None,
        )
    except Exception as e:  # binding absent, lib not loadable, attach timeout
        return NoCurrentSensor(threshold_a=threshold_a), str(e)


# vim: tabstop=4 shiftwidth=4 expandtab
