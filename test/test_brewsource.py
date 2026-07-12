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

"""Tests for brewsource: the scale/brains/potstate -> PotState seam."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import brewsource  # noqa: E402
import potstate  # noqa: E402


SETTINGS = {
    "pot_tare_g": 795,
    "pot_capacity_ml": 1250,
    "empty_thresh_g": 50,
    "stale_hours": 4.0,
}


class ScriptedScale:
    """Fake scale returning a queued list of (raw_grams, valid) each poll."""

    def __init__(self, samples):
        self._samples = list(samples)
        self._cur = (0.0, False)

    def poll(self):
        if self._samples:
            self._cur = self._samples.pop(0)

    @property
    def weight_is_valid(self):
        return self._cur[1]

    @property
    def weight(self):
        return self._cur[0]


class FailingScale:
    def poll(self):
        raise OSError("serial boom")

    weight_is_valid = False
    weight = 0.0


class TestScaleBrewSource(unittest.TestCase):
    def test_no_pot_below_tare(self):
        sc = ScriptedScale([(0.0, True)])
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        r = src.poll()
        self.assertEqual(r.pot_state.key, "no_pot")
        self.assertTrue(r.valid)

    def test_full_pot_reads_fresh(self):
        # a steady full pot -> Brains "ready", potstate "fresh"
        sc = ScriptedScale([(795 + 900, True)] * 6)
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        r = None
        for _ in range(6):
            r = src.poll()
        self.assertIn(r.pot_state.key, ("fresh", "aging"))
        self.assertAlmostEqual(r.raw_grams, 795 + 900)

    def test_serial_error_is_swallowed(self):
        src = brewsource.ScaleBrewSource(FailingScale(), SETTINGS)
        r = src.poll()  # must not raise
        self.assertFalse(r.valid)
        self.assertTrue(r.moving)  # invalid read reported as "moving"
        self.assertEqual(r.pot_state.key, "no_pot")  # held initial state

    def test_invalid_holds_last_valid_state(self):
        # A valid full-pot read, then an invalid (moving) read: the moving
        # read must keep showing the last good state, not blank to no_pot.
        sc = ScriptedScale([(795 + 900, True), (0.0, False)])
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        good = src.poll()
        self.assertTrue(good.valid)
        self.assertIn(good.pot_state.key, ("fresh", "aging"))
        moving = src.poll()
        self.assertFalse(moving.valid)
        self.assertTrue(moving.moving)
        # same held state, not "no_pot"/"unavailable"
        self.assertEqual(moving.pot_state.key, good.pot_state.key)

    def test_valid_read_clears_moving(self):
        sc = ScriptedScale([(795 + 900, True)])
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        r = src.poll()
        self.assertFalse(r.moving)

    def test_brew_then_ready_surfaces_event(self):
        # rising weight (brewing) then steady (ready) -> event "ready"
        rising = [(795 + 100 * i, True) for i in range(1, 5)]  # brewing
        steady = [(795 + 900, True)] * 70  # drain window
        sc = ScriptedScale(rising + steady)
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        seen_ready = False
        for _ in range(len(rising) + len(steady)):
            r = src.poll()
            if r.event == "ready":
                seen_ready = True
        self.assertTrue(seen_ready)


class TestMockBrewSource(unittest.TestCase):
    def test_cycles_states(self):
        states = [
            potstate.PotState("no_pot", "No pot"),
            potstate.PotState("fresh", "Fresh", fill=0.7),
            potstate.PotState("stale", "Stale", fill=0.5, expired=True),
        ]
        src = brewsource.MockBrewSource(states)
        self.assertEqual(src.poll().pot_state.key, "no_pot")
        src.advance()
        self.assertEqual(src.poll().pot_state.key, "fresh")
        src.advance()
        self.assertEqual(src.poll().pot_state.key, "stale")
        src.advance()  # wraps
        self.assertEqual(src.poll().pot_state.key, "no_pot")


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
