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
→ `ready`; `ready_time` set; a `ready` event is published to MQTT.

**A3. Brew stalls short → drag the line down to complete.** ✅🟡
Grounds absorbed more than expected; the level plateaus below the target line.
→ stays `brewing`; user drags the target line DOWN to the level actually
reached → fill meets line → completes to `ready`. No separate button, and it
self-calibrates (the line position persists) over a few brews.

**A4. A dribble / small addition while idle.** ✅
Weight rises a little with no BREW pressed.
→ nothing: stays `idle`, no `brewing`, no "fresh", no notification. (This is
the bug the explicit model fixes — inference used to flash "fresh coffee".)

**A5. Brew with the flow selector shut (overflow).** ✅🟡
A common failure: the Moccamaster's flow selector is left shut (e.g. after
washing the basket), so the boiler heats and brews but nothing reaches the
carafe and the basket overfills onto the counter.  Detected as "boiler on this
long with no weight gain" — while `brewing` with the boiler still drawing
current, the contents don't climb more than `SETTLE_EPSILON_G` above the
brew-start level for `OVERFLOW_GRACE_S` (30 s, conservative so a normal warmup
never trips it).  Requires a scale; with none there is nothing to compare, so
the guard stays quiet.
→ a sticky red **OVERFLOW** banner on Home for as long as the condition holds,
and one `overflow` alert published to MQTT on the rising edge.  It is a live
derived condition, not a state, so it self-clears the instant coffee flows
(gain exceeds epsilon) and never corrupts the idle/brewing/ready lifecycle.

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

**G1. Publish "coffee ready" to MQTT.** 🟡
On a brew reaching `ready`, publish to `<mqtt_topic_prefix>/<location>/ready`.
brewcop is notification-agnostic: a downstream MQTT consumer decides whether
to post to Slack, drive signage, etc. With `mqtt_host` empty the app runs
normally and publishes nothing. Because ready only follows an explicit BREW,
pours/placements/returns never publish — the storm is structurally impossible
now, not just tuned away.

**G2. Publish an "overflow" alert to MQTT.** 🟡
On the rising edge of an overflow (A5), publish to
`<mqtt_topic_prefix>/<location>/overflow`.  Unlike `ready` the alert is NOT
retained: overflow is edge-triggered, so a consumer reconnecting after the
spill was cleared must not be handed a stale alarm.  Fire-and-forget and
gated by the same empty-`mqtt_host` no-op.

---

## H. Power / controls

**H1. Shut down, reboot, or stop/restart the app from the screen.** 🟡
Tap the power button (upper-left of Home, standard power glyph).
→ a confirm dialog offers **Power off**, **Reboot**, **Stop app**,
**Restart app**, and Cancel. A single stray touch only opens the dialog; the
destructive action needs a second deliberate tap. Power off / reboot bring the
OS down cleanly (no plug-pull, the SD-corruption risk read-only-root guards
against); stop app leaves `brewcop.service` down (a clean stop is not a
failure, so `Restart=on-failure` does not bounce it), and restart app bounces
it to recover a wedged UI without a power-cycle. The actions run `systemctl`
as the unprivileged brewcop user,
authorized by the polkit rule the deb ships
(`/usr/share/polkit-1/rules.d/70-brewcop.rules`); a denial is flashed on the
status line rather than crashing. In `--windowed`/`--mock` dev runs the
intended command is logged, not executed, so the dev box is never powered off.

---

## Open questions

- **❓ Default full-pot level.** `brew_target_ml` defaults to
  `pot_capacity_ml` (1250). The true finished level of a "full" brew is a bit
  less (grounds retain water); the target line self-calibrates in use (A3),
  but a better default could be set from a real brew.
- **❓ Downstream notification policy.** Now that ready is gated by explicit
  BREW, the MQTT consumer can safely notify on every `ready` — decide the
  policy (which channel, quiet hours) in the consumer after on-machine
  confirmation.
