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
Brew data source: the seam between hardware and the UI.

A source exposes a single method, poll(), returning the current PotState
(from potstate.derive) plus any event ("ready") to act on.  The Kivy app
calls poll() on a timer and renders the PotState -- it does not know or care
whether the data came from a real scale or a mock.  This is what the app's
--mock flag selects between.

Kept free of Kivy so it is unit-testable headlessly.  Slack notification is
NOT done here; poll() surfaces the "ready" event and the app decides whether
to notify (gated by config, and off until the detector is retuned).
"""

import potstate
from brains import Brains


class PollResult:
    """What a source returns each tick."""

    def __init__(
        self, pot_state, event=None, raw_grams=None, valid=False, moving=False
    ):
        self.pot_state = pot_state  # potstate.PotState
        self.event = event  # "ready" or None
        self.raw_grams = raw_grams  # last raw scale reading (g) or None
        self.valid = valid  # was the last weight valid
        self.moving = moving  # scale in motion / reading not yet stable


class ScaleBrewSource:
    """
    Real source: poll the scale, feed Brains, derive the PotState.

    `scale` is any object with .poll(), .weight_is_valid, and .weight
    (grams, pre-tare) -- i.e. scale.Scale or scale.NoScale.  `settings` is a
    mapping providing pot_tare_g, pot_capacity_ml, empty_thresh_g,
    stale_hours (the user settings; config is the single source of truth).
    """

    def __init__(self, scale, settings, tick_period=0.5):
        self._scale = scale
        self._settings = settings
        self._brains = Brains(
            tick_period=tick_period,
            empty_thresh=settings["empty_thresh_g"],
        )
        # Last valid PotState, held through brief invalid/moving readings so
        # the display doesn't flap to "unavailable" every time the scale is
        # in motion (a bump, a pour).
        self._last_pot = potstate.PotState("no_pot", "No pot on scale", 0.0, False)

    def poll(self):
        # Read the scale (never let a serial hiccup crash the caller).
        try:
            self._scale.poll()
            valid = self._scale.weight_is_valid
        except Exception:
            valid = False

        if not valid:
            # Scale is moving / reading not yet stable.  Keep showing the last
            # good state rather than blanking, and flag that we're moving so
            # the UI can show a subtle "settling" indicator.
            return PollResult(
                self._last_pot, event=None, raw_grams=None, valid=False, moving=True
            )

        raw = self._scale.weight

        # Feed Brains the *contents* weight (net of tare, tolerance applied),
        # matching how the original code stored w = weight - tare.  Brains
        # only cares about relative change + empty thresh.
        net = potstate.net_contents_g(raw, self._settings["pot_tare_g"])
        event = self._brains.store(net)

        pot = potstate.derive(
            raw_weight_g=raw,
            weight_is_valid=True,
            brew_state=self._brains.state,
            elapsed_s=self._brains.elapsed(),
            config=self._settings,
        )
        self._last_pot = pot
        return PollResult(pot, event=event, raw_grams=raw, valid=True, moving=False)


class MockBrewSource:
    """
    Mock source: cycle through a fixed list of PotStates, one per poll()
    (advanced by advance()), for running the UI without hardware.  Mirrors
    the mockup's tap-to-cycle behavior but behind the same poll() interface.
    """

    def __init__(self, states):
        # states: list of potstate.PotState
        self._states = list(states)
        self._i = 0

    def poll(self):
        st = self._states[self._i]
        return PollResult(st, event=None, raw_grams=None, valid=True)

    def advance(self):
        """Move to the next mock state (e.g. on a screen tap)."""
        self._i = (self._i + 1) % len(self._states)


# vim: tabstop=4 shiftwidth=4 expandtab
