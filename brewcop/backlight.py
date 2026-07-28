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
Control the touchscreen backlight brightness (for inactivity dimming).

Writes the panel's real backlight brightness via sysfs -- this physically
dims the LED, unlike drawing a translucent overlay (which would keep the
backlight at full power).

The sysfs device name is version-dependent: the legacy display stack used
`/sys/class/backlight/rpi_backlight/`, while Bookworm's KMS driver names it
after the I2C address, e.g. `/sys/class/backlight/10-0045/`.  So we DISCOVER
the device by globbing `/sys/class/backlight/*` rather than hardcoding a
name.

Degrades gracefully to a no-op when there is no backlight node or we lack
write permission (e.g. running on a dev box, or before the udev rule is in
place) -- so callers never have to guard against its absence.  Deployment
needs a udev rule granting the service user write access to
`/sys/class/backlight/*/brightness`.
"""

import glob
import os


SYSFS_GLOB = "/sys/class/backlight/*"


class Backlight:
    """
    Discovered backlight controller.  Use `.available` to check whether real
    control is present; all methods are safe (no-op) when it is not.
    """

    def __init__(self, base_glob=SYSFS_GLOB):
        self._dir = None
        self._max = 255
        self._level = 1.0  # last-set fraction (assume full at start)
        self._discover(base_glob)

    def _discover(self, base_glob):
        for d in sorted(glob.glob(base_glob)):
            bpath = os.path.join(d, "brightness")
            if os.path.exists(bpath):
                self._dir = d
                self._max = self._read_int(
                    os.path.join(d, "max_brightness"), default=255
                )
                # seed level from the current actual brightness if readable
                cur = self._read_int(os.path.join(d, "brightness"), default=self._max)
                self._level = cur / self._max if self._max else 1.0
                return

    @staticmethod
    def _read_int(path, default):
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return default

    @property
    def available(self):
        """True if a writable backlight brightness node was found."""
        return self._dir is not None

    @property
    def device_dir(self):
        return self._dir

    @property
    def level(self):
        """Last-set brightness as a 0..1 fraction."""
        return self._level

    def set_level(self, fraction):
        """
        Set brightness to `fraction` (0..1).  Clamped.  No-op (but still
        records the intended level) if no device or the write fails.
        Returns True if the hardware was actually written.
        """
        fraction = max(0.0, min(1.0, float(fraction)))
        self._level = fraction
        if self._dir is None:
            return False
        value = int(round(fraction * self._max))
        try:
            with open(os.path.join(self._dir, "brightness"), "w") as f:
                f.write(str(value))
            return True
        except OSError:
            return False


# vim: tabstop=4 shiftwidth=4 expandtab
