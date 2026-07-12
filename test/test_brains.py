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
Characterization tests for brains.Brains.

These pin the CURRENT (deliberately-preserved, over-eager) behavior so a
future redesign is a conscious change, not an accident.  Where a test
documents a known-bad quirk, it says so.
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
    b = brains.Brains(tick_period=1, empty_thresh=empty_thresh, now=clk)
    return b, clk


class TestBrains(unittest.TestCase):
    def test_starts_unknown(self):
        b, _ = make()
        self.assertEqual(b.state, "unknown")

    def test_rising_weight_is_brewing(self):
        b, _ = make()
        for w in (100, 200, 300):
            b.store(w)
        self.assertEqual(b.state, "brewing")

    def test_stable_full_pot_is_ready(self):
        # steady, above empty threshold, no rise -> ready
        b, _ = make()
        for _ in range(5):
            b.store(1000)
        self.assertEqual(b.state, "ready")

    def test_low_stable_is_empty(self):
        b, _ = make(empty_thresh=50)
        for _ in range(5):
            b.store(10)
        self.assertEqual(b.state, "empty")

    def test_brewing_to_ready_emits_event(self):
        b, _ = make()
        # rise -> brewing
        b.store(100)
        b.store(500)
        self.assertEqual(b.state, "brewing")
        # now feed steady (non-increasing) values until it flips to ready.
        # The window must drain of any increasing pair first.
        event = None
        for _ in range(b.history.maxlen + 1):
            e = b.store(500)
            if e:
                event = e
        self.assertEqual(b.state, "ready")
        self.assertEqual(event, "ready")

    def test_elapsed_tracks_state_entry(self):
        b, clk = make()
        for _ in range(5):
            b.store(1000)  # ready
        clk.advance(42)
        self.assertAlmostEqual(b.elapsed(), 42)

    def test_no_slack_side_effect(self):
        # store() must never reach the network; it only returns an event.
        b, _ = make()
        # (no SLACK_WEBHOOK_URL set, no requests import used) -- if store()
        # tried to POST, this would raise. It must not.
        b.store(100)
        b.store(200)
        for _ in range(b.history.maxlen + 1):
            b.store(200)
        self.assertIn(b.state, ("ready", "brewing"))

    # --- documents the KNOWN over-eager quirk (do not "fix" silently) ---
    def test_KNOWN_QUIRK_single_blip_trips_brewing(self):
        # A lone 1 g uptick anywhere in the window reads as "brewing".
        # This is the root of the notification storm; pinned intentionally.
        b, _ = make()
        b.store(1000)
        b.store(1001)  # +1 g blip
        self.assertEqual(b.state, "brewing")


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
