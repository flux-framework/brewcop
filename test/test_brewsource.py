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
        # A pot that just appears while IDLE (no explicit BREW) is "present":
        # coffee on the scale, but no active batch -> no freshness claim.
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
        # Setting a full pot on the scale while IDLE must NOT fire a "ready"
        # event -- only an explicit BREW that reaches target does.  This is
        # the notification-storm fix: no BREW pressed, so no notification.
        sc = ScriptedScale([(795 + 900, True)] * 10)
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        events = [src.poll().event for _ in range(10)]
        self.assertTrue(all(e is None for e in events))
        self.assertEqual(src.poll().pot_state.key, "present")

    def test_brew_cycle(self):
        # start_brew -> filling stays "brewing" -> reaching the dialed target
        # level emits ready
        sc = ScriptedScale([(795 + 300, True), (795 + 1200, True)])
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        src.start_brew(target_g=1200)  # finished-pot level
        r1 = src.poll()  # 300 g contents, under target
        self.assertEqual(r1.pot_state.key, "brewing")
        self.assertIsNone(r1.event)
        r2 = src.poll()  # 1200 g contents == target
        self.assertEqual(r2.event, "ready")
        self.assertIn(r2.pot_state.key, ("fresh", "aging"))
        # clean up -> back to idle; a full pot now reads "present"
        src.clean_up()
        r3 = src.poll()
        self.assertEqual(r3.pot_state.key, "present")

    def test_needs_clean_latch_survives_rebrew(self):
        # A batch goes stale (needs_clean latched via the pot_state), then a
        # NEW brew is started without cleaning: the fresh pot still carries the
        # biohazard reminder (needs_clean stays True until clean_up()).
        cfg = dict(SETTINGS, stale_hours=4.0)
        clock = [1000.0]
        sc = ScriptedScale([(795 + 1200, True)] * 8)
        src = brewsource.ScaleBrewSource(sc, cfg)
        src._brains._now = lambda: clock[0]  # injectable clock
        src.start_brew(target_g=1200)
        src.poll()  # -> ready at t=1000
        clock[0] += 4 * 3600 + 10  # age past the stale window
        r = src.poll()
        self.assertTrue(r.pot_state.needs_clean)  # latched
        # brew again WITHOUT cleaning -> still filling (target above current),
        # but the nag persists over the fresh pot
        src.start_brew(target_g=2000)
        r2 = src.poll()
        self.assertEqual(r2.pot_state.key, "brewing")
        self.assertTrue(r2.pot_state.needs_clean)
        # CLEAN clears it
        src.clean_up()
        r3 = src.poll()
        self.assertFalse(r3.pot_state.needs_clean)

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
