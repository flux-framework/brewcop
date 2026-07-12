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
Brew state machine: interpret a series of scale weights as brew activity.

Extracted verbatim (logic-wise) from the original brewcop.py Brains class so
it is importable and testable.  The DETECTION LOGIC is intentionally
unchanged -- it is known to be over-eager (any upward blip over the 30 s
window counts as "brewing", so scale jitter or setting the pot back after a
pour can trip it; this caused a Slack notification storm).  It is preserved
as-is here, with characterization tests documenting the current behavior, so
a later redesign can be tuned against real weight traces captured with
test/scale_probe.py rather than guesswork.

Two things were decoupled from the original (plumbing, not logic):
  - The Slack POST is removed.  store() instead RETURNS an event string
    ("ready") on the brewing->ready transition; the caller decides whether
    to notify.  This is the storm-prone trigger, so gating it belongs with
    the app (and stays off until the detector is retuned).
  - The urwid display property is removed.  Brains exposes `state` and
    `elapsed()`; potstate.derive() owns presentation and staleness.

States: unknown | brewing | ready | empty.
"""

import time
from collections import deque


class Brains:
    """
    Interpret contents-weight (grams, net of pot tare) as brew activity.

    The discriminator is the RATE of weight change, which cleanly separates
    the two things that both "raise the weight":

      - A real brew: the pot sits on the scale at ~empty and coffee drips in,
        so the weight rises GRADUALLY (~1.25 L over ~5-6 min, a few g/s).
        This is the only thing that makes coffee "fresh".
      - A placement: the pot returns from being carried around, or something
        is set on the scale -- the weight JUMPS hundreds of g in one sample.
        Not a brew; the coffee (if any) is whatever it already was.

    Coffee AGE is wall-clock time since the last brew completed (ready_time).
    It ticks continuously -- including while the pot is off the scale being
    passed around -- so a pot returning from a meeting reads its true age
    (and may already be stale).  ready_time is reset ONLY by a gradual brew
    that then settles; every other change (removal, return heavier or
    lighter, pouring, a cup tipped back in) preserves it.

    Rate thresholds are provisional constants (grams/second), to be tuned
    against real weight traces captured with test/scale_probe.py.

    store() returns "ready" on the brewing->settled transition (the point to
    notify); state is empty|brewing|ready|unknown; elapsed() gives the value
    the UI wants per state (brew duration while brewing, coffee age while
    ready).
    """

    # Provisional rate thresholds (grams/second) -- tune with real traces.
    STABLE_RATE_GPS = 1.0  # |rate| below this: weight is stable
    BREW_RATE_MAX_GPS = 30.0  # rise faster than this: a placement, not a brew
    RATE_WINDOW_S = 4.0  # window over which rate is estimated

    def __init__(self, tick_period=1, empty_thresh=0, now=time.time):
        # tick_period is accepted for API compatibility but unused: rate is
        # computed from real timestamps over RATE_WINDOW_S, not sample counts.
        self.pot_empty_thresh_g = empty_thresh
        self._now = now  # injectable clock for testing
        self._samples = deque()  # (t, net) within RATE_WINDOW_S
        self.state = "unknown"
        self.ready_time = None  # wall-clock when current coffee became ready
        self.timestamp = 0  # when the current state was entered

    def store(self, net):
        """
        Record a contents-weight sample (grams, net of tare) and update state.
        Returns "ready" on a brewing->settled transition, else None.
        """
        t = self._now()
        self._samples.append((t, net))
        while self._samples and t - self._samples[0][0] > self.RATE_WINDOW_S:
            self._samples.popleft()
        return self._update(t, net)

    def _rate(self):
        """Estimated rate of change (g/s) over the sample window."""
        if len(self._samples) < 2:
            return 0.0
        t0, n0 = self._samples[0]
        t1, n1 = self._samples[-1]
        if t1 <= t0:
            return 0.0
        return (n1 - n0) / (t1 - t0)

    def _update(self, t, net):
        prev = self.state
        rate = self._rate()

        if net <= self.pot_empty_thresh_g:
            # No coffee present (empty pot, or pot absent -> net goes very
            # negative).  Do NOT reset ready_time: coffee on a walkabout keeps
            # aging, and an emptied pot's age is moot until the next brew.
            self._set_state(t, "empty")
            return None

        # Coffee is present.
        if self.STABLE_RATE_GPS < rate <= self.BREW_RATE_MAX_GPS:
            # Gradual, sustained rise -> a brew in progress.
            self._set_state(t, "brewing")
            return None

        # Otherwise settled: stable, declining (pouring), or a step jump
        # (placement / return / pour-back) -- all mean "ready", not brewing.
        event = None
        if prev == "brewing":
            # A brew just finished: fresh coffee, reset the age clock, notify.
            self.ready_time = t
            event = "ready"
        elif self.ready_time is None:
            # First sight of coffee with no prior brew (startup, or a pot
            # placed with coffee already in it): assume fresh as of now.
            self.ready_time = t
        self._set_state(t, "ready")
        return event

    def _set_state(self, t, s):
        if self.state != s:
            self.state = s
            self.timestamp = t

    def elapsed(self, now=None):
        """
        Seconds the UI should show for the current state:
          ready   -> coffee age (now - ready_time), ticks continuously
          other   -> time in the current state (e.g. brew duration)
        """
        if now is None:
            now = self._now()
        if self.state == "ready" and self.ready_time is not None:
            return now - self.ready_time
        return now - self.timestamp


# vim: tabstop=4 shiftwidth=4 expandtab
