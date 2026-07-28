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

from brewcop import brewsource  # noqa: E402
from brewcop import potstate  # noqa: E402


SETTINGS = {
    "pot_tare_g": 795,
    "pot_capacity_ml": 1250,
}

BREW_A = 12.5  # boiler running
IDLE_A = 0.5  # boiler off


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


class ScriptedCurrentSensor:
    """Fake current sensor.  `.amps` is settable so a test can turn the boiler
    on/off across polls; `raises=True` simulates a hardware hiccup."""

    def __init__(self, amps=12.5, raises=False):
        self.amps_value = amps
        self._raises = raises

    @property
    def amps(self):
        if self._raises:
            raise OSError("phidget boom")
        return self.amps_value


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

    def test_brew_cycle_auto_detected(self):
        # Boiler current drives the whole cycle -- no BREW button.  A sustained
        # draw arms brewing; boiler-off + a settled pour completes to ready.
        from brewcop import brains

        clock = [1000.0]
        cur = ScriptedCurrentSensor(amps=BREW_A)
        sc = ScriptedScale([(795 + 1200, True)] * 6)
        src = brewsource.ScaleBrewSource(sc, SETTINGS, current_sensor=cur)
        src._brains._now = lambda: clock[0]  # injectable clock
        r1 = src.poll()  # boiler just came on; debounce not yet met
        self.assertEqual(r1.pot_state.key, "present")
        # boiler_on leads the brewing state: it's true immediately (drives the
        # rain cloud), before the debounce has armed "brewing".
        self.assertTrue(r1.boiler_on)
        clock[0] += brains.BREW_ON_DEBOUNCE_S + 1
        r2 = src.poll()  # sustained -> brewing
        self.assertEqual(r2.pot_state.key, "brewing")
        self.assertTrue(r2.boiler_on)
        cur.amps_value = IDLE_A  # boiler off, pour settling
        src.poll()
        clock[0] += brains.SETTLE_WINDOW_S + 1
        r3 = src.poll()  # settled -> ready
        self.assertEqual(r3.event, "ready")
        self.assertEqual(r3.pot_state.key, "ready")

    def test_reset_clears_clock(self):
        # Drive a full brew to ready (age clock running), then RESET: the batch
        # returns to idle and the clock is gone.  RESET does NOT re-tare, so a
        # full pot left on the scale now reads "present", not "ready".
        from brewcop import brains

        clock = [1000.0]
        cur = ScriptedCurrentSensor(amps=BREW_A)
        sc = ScriptedScale([(795 + 1200, True)] * 6)
        src = brewsource.ScaleBrewSource(sc, SETTINGS, current_sensor=cur)
        src._brains._now = lambda: clock[0]
        src.poll()  # boiler on
        clock[0] += brains.BREW_ON_DEBOUNCE_S + 1
        src.poll()  # brewing
        cur.amps_value = IDLE_A
        src.poll()  # boiler off, settling
        clock[0] += brains.SETTLE_WINDOW_S + 1
        r = src.poll()  # ready
        self.assertEqual(r.pot_state.key, "ready")
        src.reset()  # RESET pressed
        self.assertEqual(src._brains.state, "idle")
        r2 = src.poll()  # clock gone; full pot now just "present"
        self.assertEqual(r2.pot_state.key, "present")
        self.assertIsNone(r2.pot_state.age_s)
        # tare untouched by RESET (unlike zero())
        self.assertEqual(src._settings["pot_tare_g"], 795)

    def test_zero_tares_and_resets(self):
        # Zero-empty-pot: writes the current weight as the new tare and returns
        # to idle.  A carafe that weighs differently now reads "empty" (0 net).
        sc = ScriptedScale([(812, True)])  # heavier carafe than the 795 default
        src = brewsource.ScaleBrewSource(sc, dict(SETTINGS))
        src.zero()
        self.assertEqual(src._settings["pot_tare_g"], 812)
        r = src.poll()
        self.assertEqual(r.pot_state.key, "empty")

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
        self.assertEqual(r.pot_state.key, "ready")

    def test_persists_only_on_change(self):
        # Steady readings after the first shouldn't keep rewriting the file.
        persist = FakePersist()
        sc = ScriptedScale([(795 + 900, True)] * 5)
        src = brewsource.ScaleBrewSource(sc, SETTINGS, persist=persist)
        for _ in range(5):
            src.poll()
        # state settles once (unknown->present) then holds -> very few saves
        self.assertLessEqual(persist.saves, 2)

    def test_amps_absent_without_sensor(self):
        # No current sensor wired in -> amps is None (UI shows a blank).
        sc = ScriptedScale([(795 + 900, True)])
        src = brewsource.ScaleBrewSource(sc, SETTINGS)
        self.assertIsNone(src.poll().amps)

    def test_amps_rides_on_valid_result(self):
        sc = ScriptedScale([(795 + 900, True)])
        src = brewsource.ScaleBrewSource(
            sc, SETTINGS, current_sensor=ScriptedCurrentSensor(amps=12.5)
        )
        r = src.poll()
        self.assertTrue(r.valid)
        self.assertAlmostEqual(r.amps, 12.5)

    def test_amps_rides_on_moving_result(self):
        # Current is a separate device from the scale, so a moving/invalid
        # weight still carries a fresh current reading.
        sc = ScriptedScale([(0.0, False)])
        src = brewsource.ScaleBrewSource(
            sc, SETTINGS, current_sensor=ScriptedCurrentSensor(amps=0.5)
        )
        r = src.poll()
        self.assertFalse(r.valid)
        self.assertTrue(r.moving)
        self.assertAlmostEqual(r.amps, 0.5)

    def test_sensor_error_is_swallowed(self):
        # A sensor hiccup must not crash the tick; amps just comes back None.
        sc = ScriptedScale([(795 + 900, True)])
        src = brewsource.ScaleBrewSource(
            sc, SETTINGS, current_sensor=ScriptedCurrentSensor(raises=True)
        )
        r = src.poll()  # must not raise
        self.assertTrue(r.valid)
        self.assertIsNone(r.amps)

    def test_overflow_surfaced_on_stuck_brew(self):
        # Flow selector left shut: the boiler heats and stays on, but the
        # carafe weight never climbs.  Past the grace window, poll() must
        # surface overflow=True so the UI can warn.
        from brewcop import brains

        clock = [1000.0]
        cur = ScriptedCurrentSensor(amps=BREW_A)  # boiler stays on throughout
        sc = ScriptedScale([(795 + 40, True)] * 10)  # empty carafe, never climbs
        src = brewsource.ScaleBrewSource(sc, SETTINGS, current_sensor=cur)
        src._brains._now = lambda: clock[0]
        src.poll()  # boiler on; debounce not yet met
        clock[0] += brains.BREW_ON_DEBOUNCE_S + 1
        r = src.poll()  # brewing armed
        self.assertEqual(r.pot_state.key, "brewing")
        self.assertFalse(r.overflow)  # still inside the grace window
        clock[0] += brains.OVERFLOW_GRACE_S + 1
        r = src.poll()  # heater on this long with no gain -> overflow
        self.assertTrue(r.overflow)

    def test_no_overflow_on_healthy_brew(self):
        # A good brew climbing past the epsilon must never flag overflow, even
        # after the grace window has elapsed.
        from brewcop import brains

        clock = [1000.0]
        cur = ScriptedCurrentSensor(amps=BREW_A)
        sc = ScriptedScale(
            [(795 + 100, True), (795 + 100, True)]  # arm brewing at 100 g net
            + [(795 + 1200, True)] * 8  # then the carafe fills
        )
        src = brewsource.ScaleBrewSource(sc, SETTINGS, current_sensor=cur)
        src._brains._now = lambda: clock[0]
        src.poll()  # boiler on
        clock[0] += brains.BREW_ON_DEBOUNCE_S + 1
        src.poll()  # brewing armed
        clock[0] += brains.OVERFLOW_GRACE_S + 1
        r = src.poll()  # weight has climbed well past epsilon
        self.assertEqual(r.pot_state.key, "brewing")
        self.assertFalse(r.overflow)


class TestMockBrewSource(unittest.TestCase):
    def test_cycles_states(self):
        states = [
            potstate.PotState("no_pot", "No pot"),
            potstate.PotState("ready", "Ready", fill=0.7, age_s=3600),
            potstate.PotState("empty", "Empty", fill=0.02),
        ]
        src = brewsource.MockBrewSource(states)
        self.assertEqual(src.poll().pot_state.key, "no_pot")
        src.advance()
        self.assertEqual(src.poll().pot_state.key, "ready")
        src.advance()
        self.assertEqual(src.poll().pot_state.key, "empty")
        src.advance()  # wraps
        self.assertEqual(src.poll().pot_state.key, "no_pot")

    def test_synthesizes_amps(self):
        # --mock shows a live-looking current: brewing ~12.5 A, else idle.
        states = [
            potstate.PotState("brewing", "Brewing", fill=0.4),
            potstate.PotState("ready", "Ready", fill=0.7, age_s=3600),
        ]
        src = brewsource.MockBrewSource(states)
        r = src.poll()
        self.assertAlmostEqual(r.amps, 12.5)  # brewing
        self.assertTrue(r.boiler_on)  # brewing -> heater on (rain cloud)
        src.advance()
        r = src.poll()
        self.assertAlmostEqual(r.amps, 0.5)  # idle
        self.assertFalse(r.boiler_on)

    def test_overflow_flagged_on_overflow_state(self):
        # The "overflow" mock key drives a live-looking stuck brew: heater on
        # (rain cloud) with overflow=True, so --mock can exercise the banner.
        states = [
            potstate.PotState("overflow", "Brewing - 1 min", fill=0.02),
            potstate.PotState("ready", "Ready", fill=0.7, age_s=3600),
        ]
        src = brewsource.MockBrewSource(states)
        r = src.poll()
        self.assertTrue(r.overflow)
        self.assertTrue(r.boiler_on)  # heater on during an overflow
        self.assertAlmostEqual(r.amps, 12.5)
        src.advance()
        r = src.poll()
        self.assertFalse(r.overflow)  # a healthy ready state


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
