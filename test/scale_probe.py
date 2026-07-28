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
Scale serial bring-up probe + weight-trace logger.

Run this FIRST when bringing up the USB-serial scale -- before the Kivy app.
It proves the physical comms in isolation: open the port, send the ECR weigh
command in a loop, and print the raw bytes alongside the parsed weight and
status.  Debugging serial through the full GUI is miserable; this is 1:1 with
the wire.

It also appends each sample to a CSV (timestamp, raw_grams, tared_grams,
valid, status, raw_hex) so that, once the unit is at the coffee machine, we
capture real brew/pour traces to tune the brew-detection logic offline.

Usage:
    python3 scale_probe.py [--port PATH] [--interval SECONDS]
                           [--csv FILE] [--zero] [--once]

  --port      serial device, or "auto" (default: from machine config)
  --interval  seconds between polls (default 0.5)
  --csv       trace file (default: brewcop-trace-<pid>.csv in cwd)
  --zero      send ECR Zero once at startup
  --once      poll a single time and exit (quick connectivity check)

Requires python3-serial on the target.  Ctrl-C to stop.
"""

import argparse
import os
import sys
import time

# Import the shared driver + config from the repo root (this script is in test/).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from brewcop import scale as scale_mod  # noqa: E402
from brewcop import machineconfig  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description="brewcop scale serial probe")
    p.add_argument(
        "--port", default=None, help='serial device or "auto" (default: machine config)'
    )
    p.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="seconds between polls (default 0.5)",
    )
    p.add_argument("--csv", default=None, help="trace CSV path")
    p.add_argument("--zero", action="store_true", help="send ECR Zero at startup")
    p.add_argument("--once", action="store_true", help="poll once and exit")
    return p.parse_args()


def resolve_port(arg_port):
    if arg_port:
        return arg_port
    cfg = machineconfig.load()
    return cfg.serial_port


def main():
    args = parse_args()

    port = resolve_port(args.port)
    if port == "auto":
        detected = scale_mod.autodetect_port()
        print("autodetect: {}".format(detected or "(nothing found)"))
        if detected is None:
            print(
                "No USB serial device found. Candidates checked: "
                "/dev/serial/by-id/*, /dev/ttyUSB*",
                file=sys.stderr,
            )
            print(
                "Is the adapter plugged in? Try: ls -l /dev/serial/by-id/",
                file=sys.stderr,
            )
            return 2
        port = detected

    print("Opening scale on: {}".format(port))
    try:
        scale = scale_mod.Scale(port)
    except Exception as e:
        print("FAILED to open {}: {}".format(port, e), file=sys.stderr)
        print(
            "Hints: is python3-serial installed? is the user in the "
            "'dialout' group? does the device exist?",
            file=sys.stderr,
        )
        return 1
    print(
        "Port open. Framing 9600 7E1. Sending ECR 'W' every "
        "{}s. Ctrl-C to stop.".format(args.interval)
    )

    if args.zero:
        print("Sending ECR Zero...")
        try:
            scale.zero()
            print("  zero ok, status={}".format(_status_repr(scale)))
        except Exception as e:
            print("  zero failed: {}".format(e), file=sys.stderr)

    csv_path = args.csv or "brewcop-trace-{}.csv".format(os.getpid())
    new_file = not os.path.exists(csv_path)
    csv = open(csv_path, "a", buffering=1)  # line-buffered
    if new_file:
        csv.write("iso_time,epoch,raw_grams,tared_grams,valid,status,raw_hex\n")
    print("Logging trace to: {}".format(csv_path))
    print()

    n = 0
    try:
        while True:
            n += 1
            _poll_once(scale, csv, n)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped after {} polls. Trace: {}".format(n, csv_path))
    finally:
        csv.close()
    return 0


def _status_repr(scale):
    s = scale.ecr_status
    return s.decode("ascii", "replace") if s else "None"


def _poll_once(scale, csv, n):
    epoch = time.time()
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))
    try:
        raw = scale.poll()
    except Exception as e:
        print("[{:>4}] {}  POLL ERROR: {}".format(n, iso, e))
        csv.write("{},{:.3f},,,error,{},\n".format(iso, epoch, e))
        return

    raw_hex = raw.hex() if raw else ""
    valid = scale.weight_is_valid
    status = scale.status_text
    raw_g = scale._weight  # pre-tare grams
    tared_g = scale.weight  # post-tare grams

    if valid:
        wtxt = "{:8.1f} g".format(raw_g)
    else:
        wtxt = "  --.- g  ({})".format(status)

    print("[{:>4}] {}  {}  status={:<8}  raw={}".format(n, iso, wtxt, status, raw_hex))

    csv.write(
        "{},{:.3f},{:.3f},{:.3f},{},{},{}\n".format(
            iso, epoch, raw_g, tared_g, int(valid), status, raw_hex
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

# vim: tabstop=4 shiftwidth=4 expandtab
