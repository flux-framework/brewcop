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


class FakePersist:
    """In-memory stand-in for the brewstate module."""

    def __init__(self, snap=None):
        self.snap = snap
        self.saves = 0

    def load(self):
        return self.snap

    def save(self, snap):
        self.snap = dict(snap)
        self.saves += 1
        return True


class TestScaleBrewSource(unittest.TestCase):
    def test_no_pot_below_tare(self):
        sc = ScriptedScale([(0.0, True)])
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        r = src.poll()
        self.assertEqual(r.pot_state.key, "no_pot")
        self.assertTrue(r.valid)

    def test_full_pot_appears_as_present(self):
        # A full pot that just appears (no gradual on-scale brew observed --
        # ScriptedScale polls with no time delay, so it reads as a step) is
        # "present": coffee, but age unknown.  It is NOT "fresh" (that needs
        # a watched brew).
        sc = ScriptedScale([(795 + 900, True)] * 6)
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        r = None
        for _ in range(6):
            r = src.poll()
        self.assertEqual(r.pot_state.key, "present")
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
        self.assertEqual(good.pot_state.key, "present")
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

    def test_placing_full_pot_emits_no_ready(self):
        # Setting a full pot on the scale (a step jump) must NOT fire a
        # "ready" event -- only a gradual on-scale brew does.  This is the
        # notification-storm fix at the source layer.  (Brew *timing* is
        # rate-based and covered with an injectable clock in test_brains;
        # ScriptedScale here polls with no time delay, so every change reads
        # as an instantaneous step -- exactly the placement case.)
        sc = ScriptedScale([(795 + 900, True)] * 10)
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        events = [src.poll().event for _ in range(10)]
        self.assertTrue(all(e is None for e in events))
        # ...and it settles to "present" (coffee, but age unknown -- we never
        # watched it brew), NOT "fresh" and NOT "brewing"
        self.assertEqual(src.poll().pot_state.key, "present")

    def test_restores_persisted_ready_state(self):
        # A saved "ready" snapshot from a prior run is restored on init, so a
        # pot present at startup reads as ready (known age), not "present".
        snap = {"state": "ready", "ready_time": 100.0, "timestamp": 100.0}
        persist = FakePersist(snap)
        sc = ScriptedScale([(795 + 900, True)] * 3)
        src = brewsource.ScaleBrewSource(sc, SETTINGS, persist=persist)
        r = None
        for _ in range(3):
            r = src.poll()
        # restored ready_time -> a real coffee state, not "present"
        self.assertIn(r.pot_state.key, ("fresh", "aging", "stale"))

    def test_persists_only_on_change(self):
        # Steady readings after the first shouldn't keep rewriting the file.
        persist = FakePersist()
        sc = ScriptedScale([(795 + 900, True)] * 5)
        src = brewsource.ScaleBrewSource(sc, SETTINGS, persist=persist)
        for _ in range(5):
            src.poll()
        # state settles once (unknown->present) then holds -> very few saves
        self.assertLessEqual(persist.saves, 2)


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
