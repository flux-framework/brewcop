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
Phidgets i-Snail current-sensor bring-up probe + brew-trace logger.

Run this FIRST when bringing up the brew-detection current sensor -- before
wiring it into the Kivy app.  It proves the physical signal chain in
isolation: open the VINT hub's VoltageInput channel, read the i-Snail's
0-5 V output in a loop, and print the raw volts alongside the derived amps
and a brewing/idle verdict.  Debugging a sensor through the full GUI is
miserable; this is 1:1 with the wire.

Signal chain (confirmed at bring-up, see MODERNIZATION.md):
  i-Snail VC-25 (25 A full scale, 0-5 V out)  ->  1/8" phone jack/plug
  ->  Phidgets cable  ->  VINT hub (HUB0007)  ->  USB  ->  Pi.
The i-Snail does the AC RMS in hardware; we just read a DC voltage.  A live
brew measured 0.1 V idle -> 2.49 V brewing (~12.5 A), so the default
threshold of 1.0 V (~5 A) separates the two with wide margin.

It also appends each sample to a CSV (timestamp, volts, amps, brewing) so
that, once the unit is at the coffee machine, we capture real brew traces to
tune the brew-detection threshold offline.

Usage:
    python3 isnail_probe.py [--interval SECONDS] [--csv FILE] [--once]
                            [--threshold VOLTS] [--serial N]
                            [--hub-port N] [--channel N] [--not-hubport-device]

  --interval   seconds between reads (default 0.5)
  --csv        trace file (default: brewcop-isnail-<pid>.csv in cwd)
  --once       read a single sample and exit (quick connectivity check)
  --threshold  volts above which we call it "brewing" (default 1.0)
  --serial     VINT hub serial number, if more than one Phidget is attached
  --hub-port   VINT port the i-Snail is plugged into (default 0; the
               HUB0007 has a single port, so 0 is always correct)
  --channel    channel index (default 0)
  --not-hubport-device
               treat the target as a smart VINT device rather than a raw
               0-5 V sensor on the port itself (default: hub-port device)

Requires libphidget22 on the target (apt-installable); the Python side is
brewcop's own phidget22_min ctypes binding, so there is no pip dependency.
Ctrl-C to stop.
"""

import argparse
import os
import sys
import time

# Import brewcop's phidget22_min binding from the repo root (this script is
# in test/), mirroring scale_probe.py.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# i-Snail VC-25: 25 A full scale mapped linearly onto a 0-5 V output.
ISNAIL_FULL_SCALE_A = 25.0
ISNAIL_FULL_SCALE_V = 5.0
# Default brew/idle boundary in volts (~5 A); measured idle ~0.1 V,
# brewing ~2.49 V, so this sits comfortably between.
DEFAULT_THRESHOLD_V = 1.0


def volts_to_amps(volts):
    """Convert the i-Snail's 0-5 V output to RMS amps (pure; no hardware)."""
    return volts * (ISNAIL_FULL_SCALE_A / ISNAIL_FULL_SCALE_V)


def parse_args():
    p = argparse.ArgumentParser(description="brewcop i-Snail current probe")
    p.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="seconds between reads (default 0.5)",
    )
    p.add_argument("--csv", default=None, help="trace CSV path")
    p.add_argument("--once", action="store_true", help="read once and exit")
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_V,
        help="volts above which we call it brewing (default {})".format(
            DEFAULT_THRESHOLD_V
        ),
    )
    p.add_argument("--serial", type=int, default=None, help="VINT hub serial number")
    p.add_argument(
        "--hub-port",
        type=int,
        default=0,
        help="VINT hub port index (default 0; HUB0007 has one port)",
    )
    p.add_argument("--channel", type=int, default=0, help="channel index (default 0)")
    p.add_argument(
        "--not-hubport-device",
        action="store_true",
        help="target a smart VINT device, not a raw 0-5 V port sensor",
    )
    return p.parse_args()


def open_voltage_input(args):
    """
    Open and attach a Phidget VoltageInput for the i-Snail.  Raises on
    failure (import error, no device attached within the timeout, etc.);
    the caller prints hints.
    """
    # Prefer brewcop's minimal ctypes binding (no pip dependency); fall back
    # to the official Phidget22 package if it happens to be installed.  Both
    # expose the same VoltageInput method names, so nothing below changes.
    try:
        from phidget22_min import VoltageInput
    except ImportError:
        from Phidget22.Devices.VoltageInput import VoltageInput

    vin = VoltageInput()
    if args.serial is not None:
        vin.setDeviceSerialNumber(args.serial)
    if args.hub_port is not None:
        vin.setHubPort(args.hub_port)
    # A bare 0-5 V sensor wired into a VINT port is read as a "hub port
    # device" (the port itself in voltage mode), which is our wiring.
    vin.setIsHubPortDevice(not args.not_hubport_device)
    vin.setChannel(args.channel)
    vin.openWaitForAttachment(5000)  # ms
    return vin


def main():
    args = parse_args()

    print(
        "Opening i-Snail VoltageInput "
        "(serial={}, hub_port={}, channel={}, hubport_device={})".format(
            args.serial,
            args.hub_port,
            args.channel,
            not args.not_hubport_device,
        )
    )
    try:
        vin = open_voltage_input(args)
    except ImportError as e:
        print("FAILED to import Phidget22: {}".format(e), file=sys.stderr)
        print(
            "The Phidget22 Python library is not apt-packaged. Install per "
            "phidgets.com (Python bindings + libphidget22).",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print("FAILED to attach to i-Snail: {}".format(e), file=sys.stderr)
        print(
            "Hints: is the VINT hub plugged into USB? is the i-Snail in the "
            "right port? try --hub-port / --serial. Check with the Phidget "
            "Control Panel or `lsusb` for the hub.",
            file=sys.stderr,
        )
        return 1

    print(
        "Attached. Reading every {}s; brewing threshold = {:.2f} V "
        "(~{:.1f} A). Ctrl-C to stop.".format(
            args.interval, args.threshold, volts_to_amps(args.threshold)
        )
    )

    csv_path = args.csv or "brewcop-isnail-{}.csv".format(os.getpid())
    new_file = not os.path.exists(csv_path)
    csv = open(csv_path, "a", buffering=1)  # line-buffered
    if new_file:
        csv.write("iso_time,epoch,volts,amps,brewing\n")
    print("Logging trace to: {}".format(csv_path))
    print()

    n = 0
    try:
        while True:
            n += 1
            _read_once(vin, csv, n, args.threshold)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped after {} reads. Trace: {}".format(n, csv_path))
    finally:
        csv.close()
        try:
            vin.close()
        except Exception:
            pass
    return 0


def _read_once(vin, csv, n, threshold_v):
    epoch = time.time()
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))
    try:
        volts = vin.getVoltage()
    except Exception as e:
        print("[{:>4}] {}  READ ERROR: {}".format(n, iso, e))
        csv.write("{},{:.3f},,,error\n".format(iso, epoch))
        return

    amps = volts_to_amps(volts)
    brewing = volts >= threshold_v
    verdict = "BREWING" if brewing else "idle"

    print(
        "[{:>4}] {}  {:6.3f} V  {:6.2f} A  {}".format(n, iso, volts, amps, verdict)
    )
    csv.write(
        "{},{:.3f},{:.4f},{:.3f},{}\n".format(iso, epoch, volts, amps, int(brewing))
    )


if __name__ == "__main__":
    raise SystemExit(main())

# vim: tabstop=4 shiftwidth=4 expandtab
