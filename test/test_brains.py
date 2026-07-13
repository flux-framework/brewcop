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
Tests for brains.Brains: the explicit, user-driven brew state machine.

Weight is fed as CONTENTS grams (net of pot tare).  Transitions are driven by
start_brew / mark_ready / clean_up, plus weight reaching the target while
brewing.  An injectable clock makes ages deterministic.
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
    return brains.Brains(empty_thresh=empty_thresh, now=clk), clk


class TestBrains(unittest.TestCase):
    def test_starts_idle(self):
        b, _ = make()
        self.assertEqual(b.state, "idle")

    def test_brew_autocompletes_at_target(self):
        b, clk = make()
        b.start_brew(target_g=1250)
        self.assertEqual(b.state, "brewing")
        # partial fills stay brewing
        self.assertIsNone(b.store(300))
        self.assertEqual(b.state, "brewing")
        self.assertIsNone(b.store(900))
        self.assertEqual(b.state, "brewing")
        # reaching target - margin completes
        event = b.store(1250 - brains.Brains.BREW_TARGET_MARGIN_G)
        self.assertEqual(event, "ready")
        self.assertEqual(b.state, "ready")
        self.assertIsNotNone(b.ready_time)

    def test_dribble_without_brew_does_nothing(self):
        # weight appearing while IDLE is never a brew (no inference)
        b, clk = make()
        for w in (100, 400, 900, 1200):
            event = b.store(w)
            self.assertIsNone(event)
        self.assertEqual(b.state, "idle")

    def test_mark_ready_manual_fallback(self):
        b, clk = make()
        b.start_brew(target_g=1250)
        b.store(900)  # under target, still brewing
        self.assertEqual(b.state, "brewing")
        event = b.mark_ready()
        self.assertEqual(event, "ready")
        self.assertEqual(b.state, "ready")

    def test_mark_ready_noop_when_not_brewing(self):
        b, _ = make()
        self.assertIsNone(b.mark_ready())  # idle
        self.assertEqual(b.state, "idle")

    def test_clean_up_returns_to_idle(self):
        b, clk = make()
        b.start_brew(target_g=1250)
        b.store(1200)  # -> ready
        self.assertEqual(b.state, "ready")
        b.clean_up()
        self.assertEqual(b.state, "idle")
        self.assertIsNone(b.ready_time)

    def test_age_ticks_while_ready(self):
        b, clk = make()
        b.start_brew(target_g=1250)
        b.store(1200)  # ready
        clk.advance(42)
        self.assertAlmostEqual(b.elapsed(), 42)

    def test_age_persists_while_pot_absent(self):
        # ready pot carried away (net drops) keeps its age; state stays ready
        # (user-driven -- removal is not a transition)
        b, clk = make()
        b.start_brew(target_g=1250)
        b.store(1200)  # ready
        rt = b.ready_time
        clk.advance(1800)
        b.store(-800)  # pot off the scale
        self.assertEqual(b.state, "ready")
        self.assertEqual(b.ready_time, rt)
        self.assertGreaterEqual(b.elapsed(), 1800)

    def test_is_stale(self):
        b, clk = make()
        b.start_brew(target_g=1250)
        b.store(1200)  # ready
        stale = 4 * 3600
        self.assertFalse(b.is_stale(stale))
        clk.advance(stale + 10)
        self.assertTrue(b.is_stale(stale))

    def test_is_stale_only_when_ready(self):
        b, clk = make()
        stale = 4 * 3600
        self.assertFalse(b.is_stale(stale))  # idle
        b.start_brew(target_g=1250)
        clk.advance(stale + 10)
        self.assertFalse(b.is_stale(stale))  # brewing, not ready

    def test_no_notify_on_placement_while_idle(self):
        # a full pot set down while idle does not fire "ready"
        b, clk = make()
        event = b.store(1300)
        self.assertIsNone(event)
        self.assertEqual(b.state, "idle")


class TestPersistence(unittest.TestCase):
    def test_snapshot_restore_preserves_ready_age(self):
        b, clk = make()
        b.start_brew(target_g=1250)
        b.store(1200)  # ready
        snap = b.snapshot()
        clk.advance(3600)  # an hour (incl. any downtime)
        b2 = brains.Brains(empty_thresh=50, now=clk)
        b2.restore(snap)
        self.assertEqual(b2.state, "ready")
        self.assertGreater(b2.elapsed(), 3600 - 5)

    def test_snapshot_restore_resumes_brewing(self):
        b, clk = make()
        b.start_brew(target_g=1250)
        snap = b.snapshot()
        b2 = brains.Brains(empty_thresh=50, now=clk)
        b2.restore(snap)
        self.assertEqual(b2.state, "brewing")
        self.assertEqual(b2.target_g, 1250)
        # still completes at target after restore
        self.assertEqual(b2.store(1200), "ready")

    def test_restore_none_is_noop(self):
        b, _ = make()
        b.restore(None)
        self.assertEqual(b.state, "idle")


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
