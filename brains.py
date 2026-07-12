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
    """State machine over a rolling window of scale weights (grams)."""

    # Retain scale samples for this many seconds.
    history_length = 30

    def __init__(self, tick_period=1, empty_thresh=0, now=time.time):
        self.history = deque(maxlen=int(self.history_length / tick_period))
        self.pot_empty_thresh_g = empty_thresh
        self._now = now  # injectable clock for testing
        self.state = "unknown"
        self.timestamp = 0

    def increasing(self):
        """
        Return True if history shows any value greater than a later
        (older) one.  N.B. this is the over-eager test: a single upward
        blip anywhere in the window qualifies.  Preserved as-is.
        """
        samples = list(self.history)
        return any(x > y for x, y in zip(samples, samples[1:]))

    def store(self, w):
        """
        Record a scale measurement (grams) and update state.

        Returns an event string, or None:
          "ready" -> a brewing->ready transition just occurred (the point at
                     which the original code fired a Slack notification).
        """
        self.history.appendleft(w)
        return self._brewcheck()

    def _brewcheck(self):
        previous = self.state
        event = None
        if self.increasing():
            if self.state != "brewing":
                self.state = "brewing"
                self.timestamp = self._now()
        elif self.history[0] <= self.pot_empty_thresh_g:
            if self.state != "empty":
                self.state = "empty"
                self.timestamp = self._now()
        else:
            if self.state != "ready":
                self.state = "ready"
                self.timestamp = self._now()
                if previous == "brewing":
                    event = "ready"
        return event

    def elapsed(self, now=None):
        """Seconds since the current state was entered."""
        if now is None:
            now = self._now()
        return now - self.timestamp


# vim: tabstop=4 shiftwidth=4 expandtab
