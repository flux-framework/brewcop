### BREWCOP

**B**REWCOP is a **R**aspberry pi that **E**lectronically **W**eighs **CO**ffee **P**ots

A 2018 Hackathon project produced an early version of this python script,
which talks to a point-of-sale scale sitting under the Technivorm Moccamaster
at work.  It now runs as a Kivy touchscreen app that senses brewing directly
from the boiler current and publishes a `ready` event to MQTT when a fresh
pot is done; a separate consumer decides how to notify (Slack, signage, ...).
The scale is also functional for weighing beans.

### Touchscreen

The Raspberry Pi 2 used in this project has a
[Touch Screen](https://www.raspberrypi.org/products/raspberry-pi-touch-display/).

It uses the Pi DSI connector for data, and the [Pi GPIO](https://pinout.xyz/)
for power:

* Pin 4 (5V) to red wire
* Pin 6 (GND) to black wire

brewcop runs as a systemd service that drives the display directly with
[Kivy](https://kivy.org/) (SDL2 on DRM/KMS -- no X or desktop).  See
*Install* below.

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

### Install

brewcop ships as a Debian package and installs (with its systemd service)
via apt:
```
sudo apt install ./brewcop_*.deb
```
Runtime dependencies (`python3-kivy`, `python3-serial`, `python3-paho-mqtt`,
`libmtdev1`, `libphidget22`) are pulled from apt -- there is no `pip install`
on the target.  `libphidget22` (the i-Snail current sensor library) is not in
Debian proper; add the [Phidgets apt repo](https://www.phidgets.com/docs/OS_-_Linux)
first so apt can resolve it.

Machine/deployment config lives in `/etc/brewcop/config.toml` -- copy the
installed `/etc/brewcop/config.toml.example` and fill it in for the unit.

For development without hardware, run it straight from a checkout:
```
python3 -m brewcop --mock --windowed
```

### Notifications

brewcop is notification-agnostic.  On a brew reaching *ready* it publishes to
the MQTT topic `<prefix>/<location>/ready` (set `mqtt_host`, `mqtt_topic_prefix`,
and `location` in the machine config).  A separate MQTT consumer -- not this
app -- decides what to do with the event (post to Slack, drive signage, log
telemetry).  With `mqtt_host` empty, brewcop runs normally and just publishes
nothing.

#### Release

SPDX-License-Identifier: LGPL-3.0

LLNL-CODE-764420
