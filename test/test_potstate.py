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
}


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
        # carafe present (at tare), no coffee, no active batch
        self.assertEqual(derive(795, brew_state="idle").key, "empty")
        # just under the fixed empty threshold
        self.assertEqual(
            derive(795 + potstate.EMPTY_THRESH_G - 5, brew_state="idle").key, "empty"
        )

    def test_ready_runs_age_clock(self):
        # a ready batch carries the running age clock (age_s set) and shows
        # its level; no freshness judgment is made here
        s = derive(795 + 900, brew_state="ready", elapsed=60)
        self.assertEqual(s.key, "ready")
        self.assertEqual(s.age_s, 60)
        self.assertAlmostEqual(s.fill, 900 / 1250, places=3)
        self.assertIn("L", s.text)

    def test_ready_stays_ready_when_old(self):
        # no staleness: an old ready pot is still just "ready" with a bigger
        # clock -- the human reads it and decides
        s = derive(795 + 600, brew_state="ready", elapsed=10 * 3600)
        self.assertEqual(s.key, "ready")
        self.assertEqual(s.age_s, 10 * 3600)

    def test_ready_survives_emptying(self):
        # KEY behavior: pour the coffee out (net at/below the empty band) but
        # the batch is still "ready" -- only RESET stops the clock, so an
        # emptied-but-not-reset carafe keeps counting up.
        s = derive(795, brew_state="ready", elapsed=1800)
        self.assertEqual(s.key, "ready")
        self.assertEqual(s.age_s, 1800)

    def test_present_when_idle_with_coffee(self):
        # coffee on the scale but no active batch (idle) -> "present":
        # level shown, no age claim (no clock)
        s = derive(795 + 800, brew_state="idle", elapsed=0)
        self.assertEqual(s.key, "present")
        self.assertIsNone(s.age_s)
        self.assertGreater(s.fill, 0)

    def test_brewing(self):
        s = derive(795 + 400, brew_state="brewing", elapsed=30)
        self.assertEqual(s.key, "brewing")

    def test_idle_empty_pot(self):
        # an empty pot with no active batch is just "empty"
        s = derive(795, brew_state="idle", elapsed=0)
        self.assertEqual(s.key, "empty")

    def test_fill_clamped(self):
        # overfull reading clamps to 1.0
        s = derive(795 + 2000, brew_state="ready", elapsed=10)
        self.assertEqual(s.fill, 1.0)

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
