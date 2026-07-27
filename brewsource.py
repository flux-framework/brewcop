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
        self,
        pot_state,
        event=None,
        raw_grams=None,
        valid=False,
        moving=False,
        amps=None,
    ):
        self.pot_state = pot_state  # potstate.PotState
        self.event = event  # "ready" or None
        self.raw_grams = raw_grams  # last raw scale reading (g) or None
        self.valid = valid  # was the last weight valid
        self.moving = moving  # scale in motion / reading not yet stable
        self.amps = amps  # last boiler current reading (A) or None


class ScaleBrewSource:
    """
    Real source: poll the scale, feed Brains, derive the PotState.

    `scale` is any object with .poll(), .weight_is_valid, and .weight
    (grams, pre-tare) -- i.e. scale.Scale or scale.NoScale.  `settings` is a
    mapping providing pot_tare_g, pot_capacity_ml, empty_thresh_g,
    stale_hours (the user settings; config is the single source of truth).
    `current_sensor` (optional) is any object with an .amps property -- i.e.
    currentsensor.CurrentSensor or NoCurrentSensor; None means no sensor.
    """

    def __init__(
        self, scale, settings, tick_period=0.5, persist=None, current_sensor=None
    ):
        self._scale = scale
        self._settings = settings
        self._current_sensor = current_sensor
        self._brains = Brains(
            tick_period=tick_period,
            empty_thresh=settings["empty_thresh_g"],
        )
        # Optional persistence module (brewstate) so the brew age survives
        # reboots.  Restore any saved snapshot, and remember it so we only
        # write back when the durable state actually changes.
        self._persist = persist
        self._saved_snap = None
        if persist is not None:
            snap = persist.load()
            if snap:
                self._brains.restore(snap)
                self._saved_snap = self._brains.snapshot()
        # Last valid PotState, held through brief invalid/moving readings so
        # the display doesn't flap to "unavailable" every time the scale is
        # in motion (a bump, a pour).
        self._last_pot = potstate.PotState("no_pot", "No pot on scale", 0.0, False)

    def _read_amps(self):
        # Boiler current is a separate USB device from the scale, so read it
        # independently and never let a sensor hiccup crash the tick.
        if self._current_sensor is None:
            return None
        try:
            return self._current_sensor.amps
        except Exception:
            return None

    def poll(self):
        # Read the scale (never let a serial hiccup crash the caller).
        try:
            self._scale.poll()
            valid = self._scale.weight_is_valid
        except Exception:
            valid = False

        amps = self._read_amps()

        if not valid:
            # Scale is moving / reading not yet stable.  Keep showing the last
            # good state rather than blanking, and flag that we're moving so
            # the UI can show a subtle "settling" indicator.
            return PollResult(
                self._last_pot,
                event=None,
                raw_grams=None,
                valid=False,
                moving=True,
                amps=amps,
            )

        raw = self._scale.weight

        # Feed Brains the *contents* weight (net of tare, tolerance applied),
        # matching how the original code stored w = weight - tare.  Brains
        # only cares about relative change + empty thresh.
        net = potstate.net_contents_g(raw, self._settings["pot_tare_g"])
        event = self._brains.store(net)

        stale_s = float(self._settings["stale_hours"]) * 3600.0
        # Two orthogonal signals:
        #  - is_stale(): the CURRENT ready batch is itself stale -> draw the pot
        #    empty (stale coffee shouldn't look drinkable).  Goes False again
        #    once a new brew starts.
        #  - update_dirty(): a persistent "press CLEAN" latch, set once any
        #    batch goes stale and cleared ONLY by CLEAN.  It survives into the
        #    next brew, so the biohazard stays up as a reminder even over a
        #    fresh pot.  The meatbags decide when to deal with it.
        current_stale = self._brains.is_stale(stale_s)
        needs_clean = self._brains.update_dirty(stale_s)
        self._maybe_persist()

        pot = potstate.derive(
            raw_weight_g=raw,
            weight_is_valid=True,
            brew_state=self._brains.state,
            elapsed_s=self._brains.elapsed(),
            config=self._settings,
            dirty=current_stale,
            needs_clean=needs_clean,
        )
        self._last_pot = pot
        return PollResult(
            pot, event=event, raw_grams=raw, valid=True, moving=False, amps=amps
        )

    def start_brew(self, target_g):
        """BREW pressed: arm brewing toward target_g grams of contents."""
        self._brains.start_brew(target_g)
        self._maybe_persist()

    def clean_up(self):
        """CLEAN UP pressed: batch dealt with, return to idle."""
        self._brains.clean_up()
        self._maybe_persist()

    def _maybe_persist(self):
        # Save only when the durable brew state actually changed, so writes
        # are rare (kind to the writable partition).
        if self._persist is None:
            return
        snap = self._brains.snapshot()
        if snap != self._saved_snap:
            self._persist.save(snap)
            self._saved_snap = snap


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
        # Synthesize a plausible boiler current so --mock shows a live-looking
        # readout: a brewing state draws ~12.5 A, everything else sits at the
        # ~0.5 A idle standing current.
        amps = 12.5 if st.key == "brewing" else 0.5
        return PollResult(st, event=None, raw_grams=None, valid=True, amps=amps)

    def advance(self):
        """Move to the next mock state (e.g. on a screen tap)."""
        self._i = (self._i + 1) % len(self._states)

    # Transition methods are no-ops in mock (states are canned + cycled).
    def start_brew(self, target_g):
        pass

    def clean_up(self):
        pass


# vim: tabstop=4 shiftwidth=4 expandtab
