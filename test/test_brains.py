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
Tests for brains.Brains: the current-driven brew state machine.

Each tick feeds store(net, amps): contents grams (net of pot tare, or None
when the scale read is invalid) and boiler current in amps (or None with no
sensor).  Transitions are driven by boiler current -- a sustained draw arms
brewing; boiler-off plus a settled pour completes to ready.  An injectable
clock makes the debounce, settle window, and ages deterministic.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from brewcop import brains  # noqa: E402


BREW_A = 12.5  # a running boiler
IDLE_A = 0.5  # boiler off, standing current


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make():
    clk = FakeClock()
    return brains.Brains(now=clk), clk


def arm_brewing(b, clk, net=300):
    """Drive idle -> brewing: boiler on, then past the debounce."""
    b.store(net, amps=BREW_A)
    clk.advance(brains.BREW_ON_DEBOUNCE_S + 1)
    b.store(net, amps=BREW_A)


def finish_brew(b, clk, net=1250):
    """Drive brewing -> ready: boiler off, pour steady past the settle window."""
    b.store(net, amps=IDLE_A)  # boiler off, start settling
    clk.advance(brains.SETTLE_WINDOW_S + 1)
    return b.store(net, amps=IDLE_A)


class TestBrains(unittest.TestCase):
    def test_starts_idle(self):
        b, _ = make()
        self.assertEqual(b.state, "idle")

    def test_boiler_blip_does_not_arm(self):
        # A brief boiler pulse shorter than the debounce must not start a brew.
        b, clk = make()
        b.store(300, amps=BREW_A)
        clk.advance(brains.BREW_ON_DEBOUNCE_S - 1)
        b.store(300, amps=IDLE_A)  # dropped before the debounce elapsed
        self.assertEqual(b.state, "idle")

    def test_boiler_on_leads_brewing_state(self):
        # boiler_on flips true on the first hot reading -- before the debounce
        # arms the brewing state -- so the UI can start the rain cloud at once.
        b, clk = make()
        b.store(300, amps=BREW_A)
        self.assertTrue(b.boiler_on)  # heater sensed immediately
        self.assertEqual(b.state, "idle")  # but not armed yet (debounce)
        b.store(300, amps=IDLE_A)
        self.assertFalse(b.boiler_on)  # and clears when the heater stops

    def test_sustained_boiler_arms_brewing(self):
        b, clk = make()
        b.store(300, amps=BREW_A)
        self.assertEqual(b.state, "idle")  # not yet past debounce
        clk.advance(brains.BREW_ON_DEBOUNCE_S + 1)
        b.store(320, amps=BREW_A)
        self.assertEqual(b.state, "brewing")

    def test_brew_completes_on_settled_pour(self):
        # After boiler-off, a pour that stops climbing for the settle window
        # completes to ready.
        b, clk = make()
        arm_brewing(b, clk, net=300)
        # Boiler off, but coffee still dripping in -> stays brewing.
        b.store(800, amps=IDLE_A)
        self.assertEqual(b.state, "brewing")
        clk.advance(10)
        b.store(1200, amps=IDLE_A)  # still climbing -> resets settle window
        self.assertEqual(b.state, "brewing")
        # Now steady (within the settle epsilon) for the full window -> ready.
        clk.advance(brains.SETTLE_WINDOW_S + 1)
        event = b.store(1202, amps=IDLE_A)
        self.assertEqual(event, "ready")
        self.assertEqual(b.state, "ready")

    def test_climbing_resets_settle_window(self):
        # The drip tail: weight creeping up beyond the epsilon keeps restarting
        # the window, so we don't call ready mid-pour.
        b, clk = make()
        arm_brewing(b, clk, net=300)
        net = 600
        b.store(net, amps=IDLE_A)  # boiler off
        for _ in range(3):  # keeps climbing each near-window
            clk.advance(brains.SETTLE_WINDOW_S - 1)
            net += 200
            last = b.store(net, amps=IDLE_A)
            self.assertIsNone(last)
            self.assertEqual(b.state, "brewing")

    def test_still_heating_does_not_settle(self):
        # Boiler still drawing current -> never begins the settle countdown.
        b, clk = make()
        arm_brewing(b, clk, net=300)
        clk.advance(brains.SETTLE_WINDOW_S + 5)
        self.assertIsNone(b.store(1200, amps=BREW_A))  # still on
        self.assertEqual(b.state, "brewing")

    def test_no_sensor_never_leaves_idle(self):
        # amps None (no current sensor) -> boiler never reads "on", so weight
        # alone never arms a brew.  Safe degradation: pot just shows present.
        b, clk = make()
        for w in (100, 400, 900, 1200):
            clk.advance(30)
            self.assertIsNone(b.store(w, amps=None))
        self.assertEqual(b.state, "idle")

    def test_no_scale_settles_on_fallback_timer(self):
        # net None (no scale): can't watch weight, so ready fires a fixed spell
        # after boiler-off.
        b, clk = make()
        b.store(None, amps=BREW_A)
        clk.advance(brains.BREW_ON_DEBOUNCE_S + 1)
        b.store(None, amps=BREW_A)
        self.assertEqual(b.state, "brewing")
        b.store(None, amps=IDLE_A)  # boiler off
        clk.advance(brains.SETTLE_FALLBACK_S + 1)
        self.assertEqual(b.store(None, amps=IDLE_A), "ready")

    def test_ready_time_is_settle_not_boiler_off(self):
        # Freshness clock starts when the pour settles, not when the boiler
        # cut out -- the drip tail shouldn't count against the coffee's age.
        b, clk = make()
        arm_brewing(b, clk, net=300)
        b.store(1200, amps=IDLE_A)  # boiler off here
        clk.advance(brains.SETTLE_WINDOW_S + 1)
        settle_t = clk.t
        b.store(1200, amps=IDLE_A)  # ready fires now
        self.assertEqual(b.ready_time, settle_t)

    def test_rebrew_from_ready_without_zeroing(self):
        # Pouring out an old batch and starting a fresh brew without pressing
        # Zero must still re-arm brewing -- the boiler tells us, so the cycle
        # doesn't get stuck in ready.
        b, clk = make()
        arm_brewing(b, clk)
        finish_brew(b, clk)
        self.assertEqual(b.state, "ready")
        b.store(300, amps=BREW_A)  # boiler fires again
        clk.advance(brains.BREW_ON_DEBOUNCE_S + 1)
        b.store(320, amps=BREW_A)
        self.assertEqual(b.state, "brewing")
        self.assertIsNone(b.ready_time)  # old batch's age cleared

    def test_reset_returns_to_idle(self):
        b, clk = make()
        arm_brewing(b, clk)
        finish_brew(b, clk)
        self.assertEqual(b.state, "ready")
        b.reset()
        self.assertEqual(b.state, "idle")
        self.assertIsNone(b.ready_time)

    def test_reset_disarms_boiler_tracking(self):
        # After reset, a still-hot boiler reading shouldn't instantly re-arm:
        # the debounce restarts from the reset.
        b, clk = make()
        arm_brewing(b, clk)
        b.reset()
        b.store(300, amps=BREW_A)  # boiler still on right after reset
        self.assertEqual(b.state, "idle")  # debounce restarts, not armed yet

    def test_age_ticks_while_ready(self):
        b, clk = make()
        arm_brewing(b, clk)
        finish_brew(b, clk)
        clk.advance(42)
        self.assertAlmostEqual(b.elapsed(), 42)

    def test_age_persists_while_pot_absent(self):
        # A ready pot carried away (net drops, then invalid) keeps its age;
        # removal is not a transition.
        b, clk = make()
        arm_brewing(b, clk)
        finish_brew(b, clk)
        rt = b.ready_time
        clk.advance(1800)
        b.store(None, amps=IDLE_A)  # pot off the scale
        self.assertEqual(b.state, "ready")
        self.assertEqual(b.ready_time, rt)
        self.assertGreaterEqual(b.elapsed(), 1800)


class TestPersistence(unittest.TestCase):
    def test_snapshot_restore_preserves_ready_age(self):
        b, clk = make()
        arm_brewing(b, clk)
        finish_brew(b, clk)
        snap = b.snapshot()
        clk.advance(3600)  # an hour (incl. any downtime)
        b2 = brains.Brains(now=clk)
        b2.restore(snap)
        self.assertEqual(b2.state, "ready")
        self.assertGreater(b2.elapsed(), 3600 - 5)

    def test_snapshot_restore_resumes_brewing(self):
        b, clk = make()
        arm_brewing(b, clk)
        snap = b.snapshot()
        b2 = brains.Brains(now=clk)
        b2.restore(snap)
        self.assertEqual(b2.state, "brewing")
        # A settled pour after restore still completes to ready.
        b2.store(1250, amps=IDLE_A)
        clk.advance(brains.SETTLE_WINDOW_S + 1)
        self.assertEqual(b2.store(1250, amps=IDLE_A), "ready")

    def test_restore_none_is_noop(self):
        b, _ = make()
        b.restore(None)
        self.assertEqual(b.state, "idle")


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
