# brewcop use cases

A catalog of the scenarios brewcop is meant to handle, with expected
behavior. Use it to check the current code from time to time.

**Model:** brewing is explicit and user-driven — the scale measures, the
user declares intent. The brew state machine (`brains.py`) is:

```
idle ── BREW ──▶ brewing ──[fill reaches the target line]──▶ ready
  ▲                                                             │
  └──────────────────── CLEAN UP ───────────────────────────────┘
```

The brew amount is set by dragging a dotted target line on the carafe (the
finished-pot level).  Actions live in a fixed right-hand rail (BREW / CLEAN /
WEIGH) present on every screen; buttons enable/disable by state but never
move (BREW only when idle, CLEAN only when ready, WEIGH always).

Age = wall-clock since the batch reached ready (ticks even while the pot is
carried around). Biohazard = a ready batch aged past the stale timeout.

Notation: **weight** = raw scale reading; **contents** = weight − pot tare
(±4 g tolerance).

Where things live: brains.py (state machine) · potstate.py (state→display) ·
brewsource.py (scale+brains+potstate glue, persistence) · brewcop.py (UI).

Status: ✅ implemented & unit-tested · 🟡 implemented, needs on-panel check ·
⚠️ provisional / needs real-trace tuning · ❓ open question.

---

## A. Brewing (explicit)

**A1. Start a brew.** 🟡
Drag the dotted target line on the carafe to the finished-pot level (persists
across reboots), press **BREW** on the rail.
→ state `brewing`; carafe fills toward the target line as coffee drips in.

**A2. Brew completes at the target line.** 🟡
While brewing, contents reach the target-line level (which IS the
finished-pot level, so it's an exact match within scale noise
±POT_TOLERANCE_G — no absorption margin, no fudge constant).
→ `ready`; `ready_time` set; Slack "ready" fires (if enabled).

**A3. Brew stalls short → drag the line down to complete.** ✅🟡
Grounds absorbed more than expected; the level plateaus below the target line.
→ stays `brewing`; user drags the target line DOWN to the level actually
reached → fill meets line → completes to `ready`. No separate button, and it
self-calibrates (the line position persists) over a few brews.

**A4. A dribble / small addition while idle.** ✅
Weight rises a little with no BREW pressed.
→ nothing: stays `idle`, no `brewing`, no "fresh", no notification. (This is
the bug the explicit model fixes — inference used to flash "fresh coffee".)

---

## B. Coffee lifecycle (after ready)

**B1. Ages fresh → aging → stale.** ✅
A ready batch ages by wall-clock.
→ `fresh` → `aging` at half the stale timeout → `stale` (biohazard) at the
stale timeout (default 4 h).

**B2. Pouring cups.** 🟡
Weight steps down as people pour.
→ stays `ready`; level drops; age keeps counting from `ready_time`.

**B3. Pot carried to a meeting and returned.** ✅
Removed (→ "no pot") and later returned. State is user-driven, so removal is
NOT a transition — it stays `ready`.
→ age kept ticking the whole time; returns showing its TRUE age (may already
be stale → biohazard on arrival). No notification on return.

---

## C. Cleaning

**C1. Stale coffee, then CLEAN UP.** 🟡
Biohazard showing. Dump the coffee (→ empty), press **CLEAN UP**.
→ back to `idle`; biohazard clears. CLEAN UP is the only exit from ready.

**C2. Press CLEAN UP with coffee still in the pot.** 🟡
→ refused; flashes "empty the pot first". You can't wash a full pot.

**C3. Can't brew without cleaning up first.** ✅
BREW is only reachable from `idle`, and `idle` is only reached via CLEAN UP.
→ a stale/old batch can't be silently replaced; you must CLEAN UP (which is
the acknowledgment that the pot was dealt with) before a new BREW.

---

## D. Startup / persistence

**D1. Reboot mid-brew or mid-ready.** ✅
`state`, `ready_time`, `target_g` are persisted (`brewstate.py`).
→ a `ready` pot restores its TRUE age (incl. showing stale if it aged while
powered off); a `brewing` batch resumes toward its target. (Caveat: no RTC,
so age is briefly off until NTP syncs; a pot swapped while off fails safe —
good coffee shown old — and self-corrects at the next brew/clean-up.)

**D2. Coffee already on the scale at cold start (idle, no batch).** ✅
→ `present`: shows the level, no freshness claim, no timer, no biohazard, no
notification. Reaching fresh/stale requires an explicit BREW.

---

## E. Weigh mode (beans)

**E1. Weigh beans.** 🟡
Press **WEIGH** on the rail (available on every screen); place beans.
→ live raw weight, g/oz toggle, TARE zeroes; dosing hint for the configured
pot capacity (SCA 1:18 anchor); Back returns to Home.

**E2. Dosing hint tracks configured capacity.** ✅
Change Pot capacity in Settings, return to Weigh → hint recomputes.

---

## F. Display / presentation

**F1. Scale in motion.** 🟡
Reading briefly invalid (bump/press).
→ hold the last good state (no blank), show "settling" in the weight
readout.

**F2. Light object below pot tare.** 🟡
→ "No pot on scale" + the raw grams in the weight readout.

**F3. Backlight dims when idle; wakes on touch or event.** 🟡
No touch for `dim_timeout_s` → dim to `dim_level`. Touch wakes (first touch
after dim only wakes). A ready/stale change also wakes.

---

## G. Notifications

**G1. Slack "coffee ready".** 🟡 (OFF by default)
On a brew reaching `ready`, notify Slack IF `slack_enabled`. Because ready
only follows an explicit BREW, pours/placements/returns never notify — the
storm is structurally impossible now, not just tuned away.

---

## Open questions

- **❓ Default full-pot level.** `brew_target_ml` defaults to
  `pot_capacity_ml` (1250). The true finished level of a "full" brew is a bit
  less (grounds retain water); the target line self-calibrates in use (A3),
  but a better default could be set from a real brew.
- **❓ Enable Slack.** Now that ready is gated by explicit BREW, it may be
  safe to default `slack_enabled` on — decide after on-machine confirmation.
