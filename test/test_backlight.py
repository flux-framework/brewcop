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

"""Unit tests for backlight.py using a fake sysfs directory."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import backlight  # noqa: E402


def make_fake_backlight(root, name="10-0045", max_brightness=255, brightness=255):
    """Create a fake /sys/class/backlight/<name>/ tree; return its glob."""
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "max_brightness"), "w") as f:
        f.write(str(max_brightness))
    with open(os.path.join(d, "brightness"), "w") as f:
        f.write(str(brightness))
    return os.path.join(root, "*")


def read_brightness(bl):
    with open(os.path.join(bl.device_dir, "brightness")) as f:
        return int(f.read())


class TestBacklight(unittest.TestCase):
    def test_absent_is_noop(self):
        with tempfile.TemporaryDirectory() as root:
            # empty dir -> nothing to discover
            bl = backlight.Backlight(os.path.join(root, "*"))
            self.assertFalse(bl.available)
            # set_level records intent but reports no hardware write
            self.assertFalse(bl.set_level(0.5))
            self.assertAlmostEqual(bl.level, 0.5)

    def test_discover_and_write(self):
        with tempfile.TemporaryDirectory() as root:
            g = make_fake_backlight(root, max_brightness=255, brightness=255)
            bl = backlight.Backlight(g)
            self.assertTrue(bl.available)
            self.assertTrue(bl.device_dir.endswith("10-0045"))
            # starts at full (255/255)
            self.assertAlmostEqual(bl.level, 1.0)
            # dim to 15%
            self.assertTrue(bl.set_level(0.15))
            self.assertEqual(read_brightness(bl), int(round(0.15 * 255)))

    def test_clamp(self):
        with tempfile.TemporaryDirectory() as root:
            g = make_fake_backlight(root)
            bl = backlight.Backlight(g)
            bl.set_level(5.0)
            self.assertEqual(bl.level, 1.0)
            self.assertEqual(read_brightness(bl), 255)
            bl.set_level(-1.0)
            self.assertEqual(bl.level, 0.0)
            self.assertEqual(read_brightness(bl), 0)

    def test_nonstandard_max(self):
        # some panels report a different max_brightness
        with tempfile.TemporaryDirectory() as root:
            g = make_fake_backlight(root, max_brightness=100, brightness=100)
            bl = backlight.Backlight(g)
            self.assertAlmostEqual(bl.level, 1.0)
            bl.set_level(0.5)
            self.assertEqual(read_brightness(bl), 50)

    def test_seed_level_from_current(self):
        # discovering a panel already at half brightness seeds level ~0.5
        with tempfile.TemporaryDirectory() as root:
            g = make_fake_backlight(root, max_brightness=200, brightness=100)
            bl = backlight.Backlight(g)
            self.assertAlmostEqual(bl.level, 0.5)


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
