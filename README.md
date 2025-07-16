### BREWCOP

**B**REWCOP is a **R**aspberry pi that **E**lectronically **W**eighs **CO**ffee **P**ots

A 2018 Hackathon project produced an early version of this python script,
which talks to a point-of-sale scale sitting under the Technivorm Moccamaster
at work.  Slack notifications are issued when the pot transitions to
*brewing*, *ready*, or *empty*.  The scale is also functional for weighing
beans.

### Touchscreen

The Raspberry Pi 2 used in this project has a
[Touch Screen](https://www.raspberrypi.org/products/raspberry-pi-touch-display/).

It uses the Pi DSI connector for data, and the [Pi GPIO](https://pinout.xyz/)
for power:

* Pin 4 (5V) to red wire
* Pin 6 (GND) to black wire

The pi is configured to login as `brewcop` automatically and start
`brewcop.py` which takes over the display using [urwid](https://urwid.org/).
Urwood needs to be installed:
```
sudo apt install python3-urwid
```

### Scale Interface

The scale is a
[Avery-Berkel 6702 bench scale](https://drive.google.com/file/d/1n3imd2Zp-DZ9iqJYqm4FAiBpAGmSxwYa)
purchased on Ebay.

It is interfaced to the Pi via a serial port.  Since the scale runs at
standard RS-232 signal levels and the Pi serial port uses 3V3 signaling,
a [NulSom Inc. Ultra Compact RS232 to TTL Converter with Male DB9 (3.3V to 5V)](https://www.amazon.com/NulSom-Inc-Ultra-Compact-Converter/dp/B00OPU2QJ4)
is built into the DB-9 connector shell.  The converter connects to the Pi GPIO:

* Pin 1 (3V3) to red wire
* Pin 9 (GND) to black wire
* Pin 8 (UART TX) to brown wire
* Pin 10 (UART RX) to orange wire

The device appears as `/dev/ttyAMA0` on the Pi, after disabling
console output in `raspi-config`.  No NULL modem adapter was
required between the converter and the scale, which expects a
serial configuration of 9600,7N1.

The `query` program down in the `test` directory can be used to do a
quick weight query to the scale to test connectivity.
```
$ ./query
0.000000
```

### Network

The Pi 2 doesn't have on-board wifi, so a WiPi USB network dongle is used.
The hostname is `brewcop.local`.

### Slack Notifications

When configuring the brewcop "app" in Slack, select "Incoming Webhooks",
enable them, and select the channel in your workspace that brewcop should
post in.  Ensure that `SLACK_WEBHOOK_URL` is set to the URL shown in the
environment of `brewcop.py`.

#### Release

SPDX-License-Identifier: LGPL-3.0

LLNL-CODE-764420
