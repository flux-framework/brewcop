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
Brew state machine -- driven by boiler current, not user intent.

Earlier designs inferred the brew cycle from scale weight alone (unreliable:
a dribble or a placed object looked like a brew) and then, when that failed,
made the user press BREW to declare intent.  With an i-Snail clamp on the
Moccamaster boiler we now sense brewing *directly*: the boiler draws a
sustained ~12.5 A while heating and ~0.5 A idle.  So the machine tells us when
it is brewing -- no button, no weight-rate guessing.

States and transitions:

  idle ── boiler on past a debounce ──▶ brewing
                                          │  boiler off AND the pour has
                                          │  settled (weight stops climbing)
                                          ▼
                                        ready ── reset() ──▶ idle

- idle:    no active batch.  The weight still tells the UI whether a pot is
           sitting there, but nothing is claimed about freshness.
- brewing: the boiler is (or was just) running.  We watch the contents climb
           and wait for the pour to finish.
- ready:   ready_time set at the moment the pour settled (not boiler-off), so
           the freshness clock starts when the last drips land.  Coffee ages
           by wall-clock from there.

Ready detection is weight-settle: once the boiler stops, we wait for the net
weight to stop rising (no gain beyond SETTLE_EPSILON_G) for SETTLE_WINDOW_S,
which rides out the drip tail.  With no scale (net is None) we fall back to a
fixed SETTLE_FALLBACK_S after boiler-off.  With no current sensor (amps is
None) the boiler never reads "on", so we never leave idle -- the pot simply
shows as "present", the safe degradation.

reset() returns to a clean idle -- it backs both the Zero-empty-pot action
(after re-taring) and the RESET button (which just stops the age clock).

store() returns "ready" on the transition into ready (the point to notify).
elapsed() gives coffee age while ready, else time in the current state.
"""

import time

from .scale import POT_TOLERANCE_G


# Boiler on/off detected from clamp current with hysteresis, so noise around
# the boundary can't chatter the state.  A live brew measured ~12.5 A against
# ~0.5 A idle, leaving wide margin on both sides of this band.
BOILER_ON_A = 5.0  # at/above -> boiler considered running
BOILER_OFF_A = 2.0  # at/below -> boiler considered off

# The boiler must stay on this long before we call it a brew, so a brief
# self-clean pulse or inrush blip doesn't arm brewing.
BREW_ON_DEBOUNCE_S = 5.0

# Ready = the pour has settled.  After boiler-off, the net weight must not
# gain more than SETTLE_EPSILON_G for SETTLE_WINDOW_S before we call it ready,
# which lets the drip tail finish.  SETTLE_FALLBACK_S is the no-scale path:
# with no weight to watch, just wait a fixed spell after boiler-off.
SETTLE_WINDOW_S = 20.0
SETTLE_EPSILON_G = 5.0
SETTLE_FALLBACK_S = 20.0

# Overflow guard: a common failure is someone leaving the Moccamaster's flow
# selector shut (e.g. after washing the basket).  The boiler then heats and
# brews, but nothing reaches the carafe -- the basket overfills onto the
# counter.  We catch it as "heater running this long with no weight gain":
# once brewing, if the contents have not climbed more than SETTLE_EPSILON_G
# above the level at brew-arm for OVERFLOW_GRACE_S while the boiler is still
# on, flag overflow.  The grace rides out the machine's normal pre-flow warmup
# (the boiler heats before the first drops land); 60 s is a deliberately
# conservative default -- long enough never to false-alarm a good brew, retune
# from a real trace if needed.  Requires a scale (net weight); with no scale
# there is nothing to compare, so the guard simply never fires.
OVERFLOW_GRACE_S = 60.0


class Brains:
    """Current-driven idle/brewing/ready state machine over contents weight."""

    def __init__(self, now=time.time):
        self._now = now  # injectable clock for testing
        self.state = "idle"
        self.ready_time = None  # wall-clock when the pour settled
        self.timestamp = 0  # when the current state was entered
        self._net = 0.0  # last contents-weight sample (grams, net of tare)
        # Boiler-current tracking (hysteresis state + when it last came on).
        self._boiler_on = False
        self._boiler_on_since = None
        # Weight-settle tracking while brewing: the running fill peak and when
        # the pour last stopped rising (start of the settle window).
        self._brew_peak_net = 0.0
        self._settle_since = None
        # Overflow tracking: the contents level when brewing armed (baseline to
        # measure "has any coffee arrived") and whether we had a weight signal
        # to seed it -- with no scale there is nothing to compare against.
        self._brew_start_net = 0.0
        self._brew_had_net = False

    # --- current-driven core -------------------------------------------
    def _update_boiler(self, amps):
        """Fold a current reading into the hysteretic boiler-on flag.  amps is
        None when no sensor is present -> hold (so no sensor never arms)."""
        if amps is None:
            on = self._boiler_on  # can't tell; hold last known
        elif amps >= BOILER_ON_A:
            on = True
        elif amps <= BOILER_OFF_A:
            on = False
        else:
            on = self._boiler_on  # inside the hysteresis band; hold
        self._boiler_on = on
        if on:
            if self._boiler_on_since is None:
                self._boiler_on_since = self._now()
        else:
            self._boiler_on_since = None

    def store(self, net, amps=None):
        """
        Record a tick: contents weight (grams net of tare, or None when the
        scale reading is invalid/absent) and boiler current (amps, or None
        with no sensor).  Advances the state machine and returns "ready" on
        the transition into ready, else None.
        """
        self._update_boiler(amps)
        if net is not None:
            self._net = net
        now = self._now()

        if self.state != "brewing":
            # Arm brewing once the boiler has run continuously past the
            # debounce (a brief blip won't get here -- _boiler_on_since resets
            # whenever the boiler reads off).  Reachable from idle AND from
            # ready: a fresh brew started over an un-zeroed old batch is still
            # a brew, so the cycle re-arms without needing the Zero button.
            if (
                self._boiler_on
                and self._boiler_on_since is not None
                and (now - self._boiler_on_since) >= BREW_ON_DEBOUNCE_S
            ):
                self._brew_peak_net = net if net is not None else 0.0
                self._settle_since = None
                self._brew_start_net = net if net is not None else 0.0
                self._brew_had_net = net is not None
                self.ready_time = None
                self._set_state("brewing")
            return None

        if self.state == "brewing":
            # If the scale was invalid at arm-time, seed the overflow baseline
            # from the first valid reading we get while brewing (and start the
            # grace clock then, via the brewing-entry timestamp -- close
            # enough, since the debounce already elapsed).
            if net is not None and not self._brew_had_net:
                self._brew_start_net = net
                self._brew_peak_net = net
                self._brew_had_net = True
            # Weight still climbing (beyond noise) -> the pour isn't done;
            # restart the settle window.
            if net is not None and net > self._brew_peak_net + SETTLE_EPSILON_G:
                self._brew_peak_net = net
                self._settle_since = None
            if self._boiler_on:
                # Still heating -- not settling yet.
                self._settle_since = None
                return None
            # Boiler off: time how long the pour has been stable.
            if self._settle_since is None:
                self._settle_since = now
            window = SETTLE_WINDOW_S if net is not None else SETTLE_FALLBACK_S
            if (now - self._settle_since) >= window:
                return self._become_ready()
            return None

        # ready: the age clock is handled by elapsed(); nothing to advance.
        return None

    def _become_ready(self):
        self.ready_time = self._now()
        self._settle_since = None
        self._set_state("ready")
        return "ready"

    def reset(self):
        """Return to a clean idle: clears any ready batch and brew tracking.
        Backs both the ZERO action (after re-taring) and the RESET button
        (which just stops the age clock without touching the tare)."""
        self.ready_time = None
        self._boiler_on = False
        self._boiler_on_since = None
        self._brew_peak_net = 0.0
        self._settle_since = None
        self._brew_start_net = 0.0
        self._brew_had_net = False
        self._net = 0.0
        self._set_state("idle")

    def _set_state(self, s):
        if self.state != s:
            self.state = s
            self.timestamp = self._now()

    # --- derived --------------------------------------------------------
    @property
    def boiler_on(self):
        """The raw hysteretic boiler-on flag, before the brew-arm debounce.
        The UI uses this to show the rain cloud the instant the heater fires,
        rather than waiting out BREW_ON_DEBOUNCE_S for the state to reach
        'brewing'."""
        return self._boiler_on

    @property
    def overflow(self):
        """True when a brew appears to be running with nothing reaching the
        carafe -- the boiler is on, we are brewing, and the contents have not
        climbed past SETTLE_EPSILON_G above the brew-start level for at least
        OVERFLOW_GRACE_S.  The classic cause is the flow selector left shut, so
        the basket overfills.  Requires a weight signal (self._brew_had_net);
        with no scale we can't tell, so it stays False.  Self-clearing: the
        instant coffee starts flowing the gain exceeds epsilon and this drops
        back to False.  A live derived condition, deliberately NOT a state, so
        it can't corrupt the idle/brewing/ready lifecycle."""
        if self.state != "brewing" or not self._boiler_on:
            return False
        if not self._brew_had_net:
            return False
        gained = self._brew_peak_net - self._brew_start_net
        if gained > SETTLE_EPSILON_G:
            return False
        return (self._now() - self.timestamp) >= OVERFLOW_GRACE_S

    def elapsed(self, now=None):
        """
        Seconds the UI shows for the current state:
          ready -> coffee age (now - ready_time), ticks continuously
          else  -> time in the current state (e.g. brew duration)
        """
        if now is None:
            now = self._now()
        if self.state == "ready" and self.ready_time is not None:
            return now - self.ready_time
        return now - self.timestamp

    # --- persistence ---------------------------------------------------
    # ready_time is absolute wall-clock, so persisting it restores the *true*
    # coffee age across a reboot (even if the pot aged while powered off).
    # Boiler/settle tracking is transient -- it re-derives from live current on
    # the next tick -- so only the brew lifecycle is persisted.

    def snapshot(self):
        return {
            "state": self.state,
            "ready_time": self.ready_time,
            "timestamp": self.timestamp,
        }

    def restore(self, snap):
        if not snap:
            return
        self.state = snap.get("state", self.state)
        self.ready_time = snap.get("ready_time", self.ready_time)
        self.timestamp = snap.get("timestamp", self.timestamp)


# vim: tabstop=4 shiftwidth=4 expandtab
