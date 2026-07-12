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
Tests for brains.Brains: the rate-based brew detector.

Weight is fed as CONTENTS grams (net of pot tare); the scale/brewsource
layer handles tare and reports a large-negative net when the pot is absent.
Each test drives an injectable clock so rates are deterministic.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import brains  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make(empty_thresh=50):
    clk = FakeClock()
    b = brains.Brains(empty_thresh=empty_thresh, now=clk)
    return b, clk


def feed(b, clk, net, dt=0.5):
    """Advance the clock by dt and store one sample; return the event."""
    clk.advance(dt)
    return b.store(net)


class TestBrains(unittest.TestCase):
    def test_starts_unknown(self):
        b, _ = make()
        self.assertEqual(b.state, "unknown")

    def test_empty_pot(self):
        b, clk = make(empty_thresh=50)
        for _ in range(5):
            feed(b, clk, 10)
        self.assertEqual(b.state, "empty")

    def test_absent_pot_reads_empty(self):
        # pot off the scale -> net is very negative -> "empty"
        b, clk = make()
        for _ in range(5):
            feed(b, clk, -800)
        self.assertEqual(b.state, "empty")

    def _brew_to_full(self, b, clk, target=900, step=10):
        # Gradual fill: step g per 0.5 s tick = 2*step g/s, within brew rate.
        # Climbs past the empty threshold up to a realistic level.
        net = 0
        while net < target:
            net += step
            feed(b, clk, net)
        return net

    def _brew_and_settle(self, b, clk, target=900):
        # Brew, then hold steady long enough for the rate window to drain so
        # the state settles out of "brewing" (accept/reject fires here).
        net = self._brew_to_full(b, clk, target=target)
        for _ in range(int(brains.Brains.RATE_WINDOW_S / 0.5) + 3):
            feed(b, clk, net)
        return net

    def test_gradual_fill_is_brewing(self):
        b, clk = make()
        self._brew_to_full(b, clk)
        self.assertEqual(b.state, "brewing")

    def test_brew_then_settle_emits_ready_and_sets_age(self):
        b, clk = make()
        net = self._brew_to_full(b, clk)
        self.assertEqual(b.state, "brewing")
        # stop rising: hold steady -> settles to ready, emits event
        event = None
        for _ in range(int(brains.Brains.RATE_WINDOW_S / 0.5) + 2):
            e = feed(b, clk, net)
            if e:
                event = e
        self.assertEqual(b.state, "ready")
        self.assertEqual(event, "ready")
        # age starts near zero right after ready
        self.assertLess(b.elapsed(), 5)

    def test_step_placement_is_present_not_ready(self):
        # a full pot set down in one step (hundreds of g in 0.5 s), with no
        # prior brew observed -> "present" (age unknown), NEVER brewing, and
        # NO "ready" event -- "ready"/fresh is only reachable via a brew.
        b, clk = make()
        feed(b, clk, 0)
        event = feed(b, clk, 900)  # +900 g in one tick = 1800 g/s
        self.assertEqual(b.state, "present")
        self.assertIsNone(event)
        self.assertIsNone(b.ready_time)

    def test_age_ticks_while_pot_absent(self):
        # brew -> ready, then remove the pot; age must keep advancing so the
        # pot reads its true age when it returns
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)  # settle -> ready
        self.assertEqual(b.state, "ready")
        ready_time = b.ready_time
        # pot leaves for 30 minutes
        for _ in range(5):
            feed(b, clk, -800, dt=360)  # big time jumps, pot absent
        self.assertEqual(b.state, "empty")
        # pot returns (step) at a lower weight (some was poured)
        feed(b, clk, 600)
        self.assertEqual(b.state, "ready")
        # SAME ready_time preserved -> age reflects the ~30 min absence
        self.assertEqual(b.ready_time, ready_time)
        self.assertGreater(b.elapsed(), 30 * 60)

    def test_return_does_not_emit_ready(self):
        # returning the pot must NOT fire a notification (only a real brew does)
        b, clk = make()
        # establish a ready pot via a brew
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)
        # remove and return
        for _ in range(3):
            feed(b, clk, -800, dt=120)
        event = feed(b, clk, 700)  # step return
        self.assertIsNone(event)

    def test_startup_with_coffee_is_present_age_unknown(self):
        # a pot with coffee already present at first sight (cold start): we
        # never watched it brew, so age is unknown -> "present", no event,
        # no ready_time (do NOT claim it's fresh)
        b, clk = make()
        event = feed(b, clk, 800)
        self.assertEqual(b.state, "present")
        self.assertIsNone(event)
        self.assertIsNone(b.ready_time)

    def test_return_after_brew_shows_ready_not_present(self):
        # once a brew has been observed, a step return keeps "ready" (with the
        # preserved age), NOT "present" -- we know this coffee's age
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)  # settle -> ready
        self.assertEqual(b.state, "ready")
        for _ in range(3):
            feed(b, clk, -800, dt=120)  # pot away
        feed(b, clk, 700)  # step return
        self.assertEqual(b.state, "ready")

    # --- dirty-pot / clean-button behavior --------------------------------
    STALE = 4 * 3600

    def test_fresh_brew_becomes_dirty_when_stale(self):
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)
        self.assertFalse(b.is_dirty(self.STALE))
        clk.advance(self.STALE + 10)
        b.store(net)
        self.assertTrue(b.is_dirty(self.STALE))

    def test_rebrew_into_dirty_pot_rejected_stays_dirty(self):
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)
        rt1 = b.ready_time
        clk.advance(self.STALE + 10)
        b.store(net)  # now dirty
        # dump, then rebrew without cleaning
        feed(b, clk, -800)
        self._brew_and_settle(b, clk)
        self.assertEqual(b.ready_time, rt1)  # NOT reset -> stays dirty
        self.assertTrue(b.is_dirty(self.STALE))

    def test_dribble_below_target_not_ready(self):
        # a small gradual addition that settles well below target must NOT be
        # declared a completed brew (no ready, no event)
        b, clk = make()
        net = 0
        for _ in range(20):  # rises to ~200 g, then settle
            net += 10
            clk.advance(0.5)
            event = b.store(net, target_g=1250)
        for _ in range(int(brains.Brains.RATE_WINDOW_S / 0.5) + 3):
            clk.advance(0.5)
            e = b.store(net, target_g=1250)
            event = e or event
        self.assertNotEqual(b.state, "ready")
        self.assertIsNone(b.ready_time)

    def test_full_brew_reaches_target_is_ready(self):
        b, clk = make()
        net = 0
        while net < 1200:  # gradual fill past target-margin (1250-150=1100)
            net += 10
            clk.advance(0.5)
            b.store(net, target_g=1250)
        event = None
        for _ in range(int(brains.Brains.RATE_WINDOW_S / 0.5) + 3):
            clk.advance(0.5)
            e = b.store(net, target_g=1250)
            event = e or event
        self.assertEqual(b.state, "ready")
        self.assertEqual(event, "ready")

    def test_half_pot_target_accepts_half(self):
        # with a half-pot target, ~625 g counts as a completed brew
        b, clk = make()
        net = 0
        while net < 650:
            net += 10
            clk.advance(0.5)
            b.store(net, target_g=625)
        event = None
        for _ in range(int(brains.Brains.RATE_WINDOW_S / 0.5) + 3):
            clk.advance(0.5)
            e = b.store(net, target_g=625)
            event = e or event
        self.assertEqual(b.state, "ready")
        self.assertEqual(event, "ready")

    def test_clean_clears_dirty(self):
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)
        clk.advance(self.STALE + 10)
        b.store(net)
        self.assertTrue(b.is_dirty(self.STALE))
        b.clean()
        self.assertFalse(b.is_dirty(self.STALE))

    def test_proactive_clean_before_stale_never_dirty(self):
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)
        feed(b, clk, -800, dt=600)  # dump while fresh
        b.clean()  # wash
        self.assertFalse(b.is_dirty(self.STALE))

    def test_dirty_survives_dump_until_clean(self):
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)
        clk.advance(self.STALE + 10)
        b.store(net)
        feed(b, clk, -800)  # dump the stale coffee (no clean)
        self.assertTrue(b.is_dirty(self.STALE))  # still dirty
        b.clean()
        self.assertFalse(b.is_dirty(self.STALE))

    def test_snapshot_restore_preserves_age(self):
        # brew -> ready, snapshot; a fresh Brains that restores it reports the
        # same coffee age (ready_time is absolute, so it survives a "reboot")
        b, clk = make()
        net = self._brew_to_full(b, clk)
        for _ in range(12):
            feed(b, clk, net)
        self.assertEqual(b.state, "ready")
        snap = b.snapshot()

        clk.advance(3600)  # an hour passes (incl. any downtime)
        b2 = brains.Brains(empty_thresh=50, now=clk)
        b2.restore(snap)
        self.assertEqual(b2.state, "ready")
        # age reflects the original ready_time -> ~1 h, not zero
        self.assertGreater(b2.elapsed(), 3600 - 5)

    def test_restore_none_is_noop(self):
        b, _ = make()
        b.restore(None)  # must not raise
        self.assertEqual(b.state, "unknown")


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
