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

"""Tests for currentsensor: pure conversion + NoOp fallback + open_* helper.

The hardware path (a real CurrentSensor over phidget22_min) is exercised by
test/isnail_probe.py on the Pi, not here; these tests cover the parts that
run with neither the libphidget22 C library nor the VINT hub present.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import currentsensor  # noqa: E402


class TestAmpsFromVolts(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(currentsensor.amps_from_volts(0.0), 0.0)

    def test_full_scale(self):
        # 5 V out == 25 A full scale.
        self.assertEqual(currentsensor.amps_from_volts(5.0), 25.0)

    def test_measured_brew_point(self):
        # The live bring-up brew: 2.49 V -> ~12.5 A.
        self.assertAlmostEqual(currentsensor.amps_from_volts(2.49), 12.45)

    def test_idle_point(self):
        # ~0.1 V idle -> ~0.5 A standing current.
        self.assertAlmostEqual(currentsensor.amps_from_volts(0.1), 0.5)


class TestNoCurrentSensor(unittest.TestCase):
    def test_amps_is_none(self):
        # Reads as None (not a fabricated 0) so the UI shows a blank readout.
        self.assertIsNone(currentsensor.NoCurrentSensor().amps)

    def test_never_brewing(self):
        self.assertFalse(currentsensor.NoCurrentSensor().brewing)

    def test_close_is_noop(self):
        currentsensor.NoCurrentSensor().close()  # must not raise


class TestOpenCurrentSensor(unittest.TestCase):
    def test_falls_back_without_hardware(self):
        # On a box with no libphidget22 / no hub, open must not raise: it
        # returns a NoCurrentSensor plus a non-empty error string, mirroring
        # scale.open_scale.
        sensor, err = currentsensor.open_current_sensor()
        self.assertIsInstance(sensor, currentsensor.NoCurrentSensor)
        self.assertTrue(err)  # a descriptive message, not None/empty
        self.assertIsNone(sensor.amps)

    def test_threshold_passthrough_on_fallback(self):
        # A custom threshold still lands on the fallback sensor.
        sensor, _err = currentsensor.open_current_sensor(threshold_a=7.0)
        self.assertEqual(sensor.threshold_a, 7.0)


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
