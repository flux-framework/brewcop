# Scale hardware & protocol reference

Reference notes on the bench scale brewcop reads, for posterity. The ECR
command/response details below come from the Avery Berkel manual (see
"Sources") and match the `Scale` implementation in `brewcop.py`.

## The scale

**Avery Berkel model 6702** (6700 family: 6702 / 6710 / 6720), a
"Legal-for-Trade" digital point-of-sale bench scale. Full model string on
the original unit: `6702-16658`.

- Point-of-sale interface scale, 100,000 internal counts of resolution.
- Communicates over **RS-232** (9-pin DE female connector at the rear).
- The scale is **DTE**; it connects to a host with a **straight-through
  (pass-through) cable — NOT a null-modem cable**.
- RS-232 is bidirectional, configurable 1200–19200 baud. Our unit is
  configured for **9600 baud** (set in the scale's own MENU/config mode).

## Wiring

Original brewcop (Pi 2) used a GPIO TTL-serial converter on `/dev/ttyAMA0`.
The modernized unit uses a **USB RS-232 adapter** instead (FTDI or CP210x),
so the scale appears as `/dev/ttyUSB*` (prefer the stable
`/dev/serial/by-id/...` path). Straight-through DE-9, scale is DTE.

## Serial framing

`9600 7E1` — 9600 baud, **7 data bits, even parity, 1 stop bit**, no flow
control. (See `Scale.__init__` in `brewcop.py`.) The read timeout used is
0.25 s.

## ECR protocol (as used by brewcop)

The scale is driven in **ECR mode**. The host sends a single-letter command
terminated by CR (`\r`); the scale replies with a record terminated by the
byte **`0x03`** (read loop reads until it sees `0x03`). We only use two
commands:

| Command | Bytes sent | Purpose |
|---------|-----------|---------|
| Weigh   | `W\r`     | Request weight + status |
| Zero    | `Z\r`     | Zero the scale, returns status |

Sending ASCII **`W`+`CR`** makes the scale transmit weight and scale status;
the response layouts and status codes below are from the manual's ECR
protocol section and match what `Scale` parses.

### Weigh response — two forms

`poll()` sends `W\r` and reads one record. It comes back in one of two
lengths:

**16 bytes — valid weight + status:**

```
\n  DDDDDD  LB\r  <status-block>
^   ^       ^     ^
|   |       |     6-byte status block (see below)
|   |       literal "LB\r"  (pounds; ANSI unit abbreviation)
|   6 ASCII chars: weight in POUNDS (e.g. " 02.75")
leading \n
```

- Weight is parsed as a float from bytes `[1:7]` and converted to grams:
  **grams = pounds × 453.592**.
- The trailing 6 bytes `[10:16]` are the status block, same layout as the
  standalone status response below.

**6 bytes — status only (no valid weight):**

Returned when the scale can't give a stable weight (in motion, under/over
range, etc.):

```
\n  S  XX  \r  0x03
^   ^  ^   ^   ^
|   |  |   |   record terminator (0x03)
|   |  |   CR
|   |  2-char status code (see table)
|   literal 'S'
leading \n
```

The 2-char status code is bytes `[2:4]`.

### Status codes

Interpreted by `Scale.display` / `Scale.at_zero`:

| Code   | Meaning |
|--------|---------|
| `20`   | At zero (scale's zero LED is lit) |
| `10`, `30` | In motion / weight moving (not yet stable) |
| `01`, `11` | Under range / under capacity |
| `02`   | Over capacity |

A 16-byte response is treated as a valid weight; anything else leaves
`weight_is_valid` False.

## Coffee-pot specifics (deployment constants, not scale facts)

For the Technivorm Moccamaster **insulated (thermal) carafe**, measured
empirically and used by brewcop:

- Empty carafe tare: **~795 g** (with ±4 g tolerance for real-world
  variation in the pot's measured tare — see `POT_TOLERANCE_G`).
- Full pot capacity: **1250 mL ≈ 1250 g** (water is ~1 g/mL).
- "Empty-ish" threshold: **~50 g** of contents.

These belong in config (see the modernization plan), not hard-coded — the
scale protocol above is fixed hardware fact, but these are per-deployment.

## Sources

The command/response layout and status codes above are from the **Avery
Berkel Model 6700 Family manual** (models 6702 / 6710 / 6720), which
documents the ECR protocol, and are the basis for the `Scale` class in
`brewcop.py`. Jim has the physical manual; fill in the exact edition/part
number here when handy.

Publicly available copies of the 6700-family manual (for convenience — the
authoritative reference is the physical manual):

- manualslib: <https://www.manualslib.com/manual/636755/Avery-Berkel-6700.html>
- PDF mirror: <https://www.floridascale.com/uploads/2/5/9/0/25902411/6700_users_manual_english.pdf>
- Service manual (6700SERVC): <http://www.scaleservice.net/manuals/NCI/Scale%20Manual%206700SERVC.pdf>

Note: some online 6700-family listings only cover the *general* serial
protocol and say ECR details require contacting the factory — but the manual
Jim has documents the ECR protocol in full, which is where these details
came from.
