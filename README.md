### BREWCOP

**B**REWCOP is a **R**aspberry pi that **E**lectronically **W**eighs **CO**ffee **P**ots

A 2018 Hackathon project produced an early version of this python script,
which talks to a point-of-sale scale sitting under the Technivorm Moccamaster
at work.  It now runs as a Kivy touchscreen app that senses brewing directly
from the boiler current and publishes a `ready` event to MQTT when a fresh
pot is done; a separate consumer decides how to notify (Slack, signage, ...).
The scale is also functional for weighing beans.

### Touchscreen

The Raspberry Pi 3 used in this project has a
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
[Avery-Berkel 6702 bench scale](https://drive.google.com/file/d/1n3imd2Zp-DZ9iqJYqm4FAiBpAGmSxwYa).

It is interfaced to the Pi via a USB serial cable.
Serial configuration of 9600,7N1.

`python3 test/scale_probe.py` can be used to do a quick sanity check on
connectivity.

### Brew Sensing

An Elkor [i-Snail-VC-25 current sensor](https://www.elkor.net/product/i-Snail-VC)
reads current from the Technivorm Moccamaster AC input.  Its DC 0-5V output is
transmitted to a Phidgets [1-port USB VINT hub](https://www.phidgets.com/?prodid=1290).
This allows brewcop to directly sense when coffee is brewing.  The Moccamaster
does not have a hot plate so the current signal is unambiguous.

### Install

brewcop ships as a Debian package and installs (with its systemd service)
via apt:
```
sudo apt install ../brewcop_*.deb
```
`libphidget22` (the i-Snail current sensor library) is a dependency that is
not in Debian proper; add the [Phidgets apt repo](https://www.phidgets.com/docs/OS_-_Linux)
first so apt can resolve it.

Machine/deployment config lives in `/etc/brewcop/config.toml` -- copy the
installed `/etc/brewcop/config.toml.example` and fill it in for the unit,
or live with the defaults.

The package also ships a polkit rule
(`/usr/share/polkit-1/rules.d/70-brewcop.rules`) that lets the on-screen power
button reboot the app or the OS as the unprivileged `brewcop` user.

For development without hardware, run it straight from a checkout:
```
python3 -m brewcop --mock --windowed
```

### Notifications

On a brew reaching *ready* brewcop publishes to the MQTT topic
`<prefix>/<location>/ready` (set `mqtt_host`, `mqtt_topic_prefix`,
and `location` in the machine config).

A separate component would consume this MQTT topic and generate slack
or other types of notifications.

#### Release

SPDX-License-Identifier: LGPL-3.0

LLNL-CODE-764420
