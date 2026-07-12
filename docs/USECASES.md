# brewcop use cases

A catalog of the scenarios brewcop is meant to handle, with the expected
behavior for each. Use it to check the current code from time to time: does
the code still do the right thing for every case here?

Notation: **weight** is the raw scale reading; **contents** is weight minus
the pot tare (±4 g tolerance). "Yuck"/biohazard = the blinking symbol.

Where a case's handling lives:
- weight → state machine: `brains.py` (`Brains`)
- state → what to display: `potstate.py` (`derive`)
- gluing scale+brains+potstate, persistence: `brewsource.py`
- screens, buttons, dimming: `brewcop.py`

Status legend: ✅ implemented & unit-tested · 🟡 implemented, needs on-panel
check · ⚠️ provisional / needs real-trace tuning · ❓ open question.

---

## A. Coffee lifecycle (the core loop)

**A1. Fresh brew detected.** 🟡⚠️
Pot sits on the scale ~empty; coffee drips in gradually (~1.25 L over
~5–6 min). Weight rises slowly and sustained.
→ state `brewing`; on settle → `ready`, `ready_time` set, Slack "ready"
event fires (only if enabled). Rate thresholds are provisional (⚠️, tune
against a real trace).

**A1a. Brew must reach target level to count as done.** 🟡⚠️
Home has a Full/Half pot selector (resets to full each boot). A brew is only
declared complete when the settled level reaches the target (full =
capacity, half = capacity/2) within `BREW_TARGET_MARGIN_G` (⚠️ provisional
150 g; must cover water the grounds absorb).
→ a small addition / partial fill that settles below target does NOT become
"fresh" (fixes: a dribble on the scale flashing "fresh coffee"). It falls
through to `present` (or keeps a prior batch's age); no notification.

**A2. Coffee ages: fresh → aging → stale.** ✅
A ready pot ages by wall-clock (`now − ready_time`).
→ `fresh` → `aging` at half the stale timeout → `stale` (biohazard) at the
stale timeout (default 4 h).

**A3. Pouring cups.** ✅
Weight steps down repeatedly as people pour.
→ stays `ready`/aging; declining weight is not brewing; level drops; age
keeps counting from the original `ready_time`.

**A4. Pot emptied.** ✅
Contents drop below the empty threshold (default 50 g).
→ `empty` (unless dirty — see D). `ready_time` is not reset.

---

## B. The pot leaves the scale

**B2. Pot carried to a meeting and returned.** ✅
Removed (weight ~0 → "no pot"), sits elsewhere a while, returned — a rapid
rise to at-or-below its prior weight.
→ while absent, age keeps ticking (coffee cools on the table too). On
return: recognized as the same coffee, `ready_time` preserved, so it shows
its TRUE age (may already be stale → biohazard on arrival). No "ready"
notification on return.

**B3. Pot returned with more than it left** (someone poured a cup back). ✅
→ same pot, same age; just a higher level. Not treated as a new/fresh pot.

**B4. A step placement that is NOT a brew** (full pot set down; objects
stacked to pot weight). ✅
Weight jumps hundreds of g in one poll.
→ never `brewing`. If we have a prior `ready_time` (a pot we watched) →
`ready` with preserved age; otherwise → `present` (see C1).

---

## C. Unknown provenance

**C1. Coffee present but never observed brewing** (cold start with a pot
already there; non-coffee weight placed). ✅
→ `present`: shows the level and "age unknown", NO freshness claim, NO
timer, NO biohazard, NO notification. "ready"/fresh is only reachable by
actually watching a brew.

**C2. Reboot with coffee on the scale.** ✅
`ready_time`/`clean_time` are persisted (`brewstate.py`), so on restart the
true age is restored — including showing stale if it aged past threshold
while powered off. (Caveat: Pi has no RTC; age is briefly wrong until NTP
syncs. If the pot was swapped while off, restored age is wrong but fails
safe — good coffee shown as old, self-corrects at next brew/clean.)

---

## D. Dirty pot / cleaning

**D1. Stale coffee left sitting.** ✅
Ready pot ages past the stale timeout while still present.
→ `stale`, biohazard, "please dump".

**D2. Stale coffee dumped but pot not washed.** ✅
Dump the stale coffee (→ empty) without pressing CLEAN.
→ still yuck: the biohazard persists on the empty pot until CLEAN. (Dirty is
"went stale and not cleaned since", independent of current contents.)

**D3. Brew into a dirty (unwashed, previously-stale) pot.** ✅
Someone brews without cleaning first.
→ the new brew is REJECTED as fresh (`ready_time` not reset), so the pot
stays yuck. "Brewing without cleaning is still yuck." (Recovery = dump and
rebrew after cleaning; there is deliberately no in-place escape hatch.)

**D4. Press CLEAN with coffee still in the pot.** ✅
→ refused; flashes "empty the pot first". You can't wash a full pot.

**D5. Press CLEAN on an empty/absent pot.** ✅
→ records the cleaning (`clean_time`); biohazard clears. This is the only
thing that clears dirty.

**D6. Proactively dump + wash a still-fresh pot.** ✅
Dump while fresh (before it ever goes stale), then CLEAN.
→ never becomes dirty (dirtiness requires a batch to have gone stale). No
nag, nothing to acknowledge.

---

## E. Weigh mode (beans)

**E1. Weigh beans.** 🟡
Enter Weigh; place beans.
→ live raw weight, g/oz toggle, TARE zeroes the reading. Dosing hint shows
recommended beans for the configured pot capacity (SCA 1:18 anchor).

**E2. Dosing hint tracks configured capacity.** ✅
Change Pot capacity in Settings, return to Weigh.
→ hint recomputes (config is the single source of truth; nothing hardcoded).

---

## F. Display / presentation

**F1. Scale in motion.** 🟡
Someone bumps/presses the scale; reading is briefly invalid.
→ hold the last good state (don't blank), show "settling" in the weight
readout. No flicker to "unavailable".

**F2. Light object below pot tare.** 🟡
Something lighter than the pot tare on the platter.
→ "No pot on scale" + the raw grams shown in the weight readout (useful for
checking the tare against the real empty carafe).

**F3. Nothing on the scale.** ✅/🟡
→ "No pot on scale", blank centerpiece (no ghost carafe), weight ~0.

**F4. Backlight dims when idle; wakes on touch or event.** 🟡
No touch for `dim_timeout_s` → dim to `dim_level`. Touch wakes (and the
first touch after dimming only wakes, doesn't also hit the button). A
ready/stale state change also wakes (ambient hallway signal).

---

## G. Notifications

**G1. Slack "coffee ready".** ⚠️ (OFF by default)
On a real brewing→ready transition, notify Slack IF `slack_enabled`.
Default OFF until the detector is validated against real traces — the old
code stormed the channel on every pour. Only a watched brew notifies; pot
returns and placements do not.

---

## Open questions / not yet decided

- **❓ Rate thresholds (A1, B4).** `STABLE_RATE_GPS` / `BREW_RATE_MAX_GPS`
  are guesses; confirm/tune against a captured brew trace (`scale_probe.py`
  CSV) once the real machine + pot are available.
- **❓ "present" coffee never goes stale (C1).** A pot of unknown age never
  shows the biohazard. Acceptable? Or should unknown-age coffee become
  suspect after some time?
- **❓ Fresh brew into a just-cleaned pot vs. a pour-back** — edge cases in
  distinguishing "new batch" from "same batch, more added" beyond what B3
  covers.
