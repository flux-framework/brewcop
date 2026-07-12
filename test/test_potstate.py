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

"""Unit tests for potstate.derive() -- the scale/brew -> UI mapping."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import potstate  # noqa: E402


CONFIG = {
    "pot_tare_g": 795,
    "pot_capacity_ml": 1250,
    "empty_thresh_g": 50,
    "stale_hours": 4.0,
}
STALE_S = CONFIG["stale_hours"] * 3600.0


def derive(raw_g, brew_state="ready", elapsed=0, valid=True):
    return potstate.derive(raw_g, valid, brew_state, elapsed, CONFIG)


class TestNetContents(unittest.TestCase):
    def test_tolerance_absorbs_slop(self):
        # within +/-4 g of tare -> exactly 0 contents
        self.assertEqual(potstate.net_contents_g(795, 795), 0.0)
        self.assertEqual(potstate.net_contents_g(795 + 3, 795), 0.0)
        self.assertEqual(potstate.net_contents_g(795 - 3, 795), 0.0)

    def test_outside_tolerance(self):
        self.assertAlmostEqual(potstate.net_contents_g(795 + 100, 795), 100.0)
        # below tare-tolerance -> negative (no pot)
        self.assertLess(potstate.net_contents_g(700, 795), 0)


class TestDerive(unittest.TestCase):
    def test_no_pot_below_tare(self):
        # anything below the pot tare is "no pot" (the Home screen shows the
        # raw weight separately, so a light object's grams are still visible)
        self.assertEqual(derive(0).key, "no_pot")
        self.assertEqual(derive(320).key, "no_pot")
        self.assertEqual(derive(700).key, "no_pot")

    def test_invalid_weight(self):
        s = derive(1500, valid=False)
        self.assertEqual(s.key, "no_pot")

    def test_empty_pot(self):
        # carafe present (at tare), no coffee
        self.assertEqual(derive(795).key, "empty")
        # just under the 50 g empty threshold
        self.assertEqual(derive(795 + 40).key, "empty")

    def test_fresh(self):
        # full-ish pot, freshly ready
        s = derive(795 + 900, brew_state="ready", elapsed=60)
        self.assertEqual(s.key, "fresh")
        self.assertFalse(s.expired)
        self.assertAlmostEqual(s.fill, 900 / 1250, places=3)
        self.assertIn("L", s.text)

    def test_present_age_unknown(self):
        # coffee on the scale but brains never saw it brew -> "present":
        # level shown, no freshness claim, not expired
        s = derive(795 + 800, brew_state="present", elapsed=0)
        self.assertEqual(s.key, "present")
        self.assertFalse(s.expired)
        self.assertIn("age unknown", s.text.lower())
        self.assertGreater(s.fill, 0)

    def test_present_never_goes_stale(self):
        # even with a huge elapsed, "present" must not become stale/expired
        # (staleness requires a known ready age)
        s = derive(795 + 600, brew_state="present", elapsed=STALE_S * 10)
        self.assertEqual(s.key, "present")
        self.assertFalse(s.expired)

    def test_brewing(self):
        s = derive(795 + 400, brew_state="brewing", elapsed=30)
        self.assertEqual(s.key, "brewing")
        self.assertFalse(s.expired)

    def test_aging(self):
        # past half the stale window but not stale
        s = derive(795 + 700, brew_state="ready", elapsed=STALE_S * 0.6)
        self.assertEqual(s.key, "aging")
        self.assertFalse(s.expired)

    def test_stale_latches_expired(self):
        # past the stale window -> expired flag set (biohazard)
        s = derive(795 + 600, brew_state="ready", elapsed=STALE_S + 10)
        self.assertEqual(s.key, "stale")
        self.assertTrue(s.expired)
        self.assertIn("dump", s.text.lower())

    def test_fill_clamped(self):
        # overfull reading clamps to 1.0
        s = derive(795 + 2000, brew_state="ready", elapsed=10)
        self.assertEqual(s.fill, 1.0)

    def test_empty_pot_never_expired(self):
        # an empty pot, even long after ready, is just "empty" (no nag)
        s = derive(795, brew_state="ready", elapsed=STALE_S * 5)
        self.assertEqual(s.key, "empty")
        self.assertFalse(s.expired)

    def test_config_capacity_drives_fill(self):
        # smaller configured capacity -> larger fill fraction for same grams
        cfg = dict(CONFIG, pot_capacity_ml=1000)
        s = potstate.derive(795 + 500, True, "ready", 10, cfg)
        self.assertAlmostEqual(s.fill, 0.5, places=3)


class TestFmtElapsed(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(potstate.fmt_elapsed(30), "30s")
        self.assertEqual(potstate.fmt_elapsed(90), "1 min")
        self.assertEqual(potstate.fmt_elapsed(3600 * 2 + 600), "2h 10m")
        self.assertEqual(potstate.fmt_elapsed(86400 + 3600 * 3), "1d 3h")


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
