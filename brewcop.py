#!/usr/bin/env python3

#############################################################
# Copyright 2018 Lawrence Livermore National Security, LLC
# (c.f. AUTHORS, NOTICE.LLNS, COPYING)
#
# This file is part of the Flux resource manager framework.
# For details, see https://github.com/flux-framework.
#
# SPDX-License-Identifier: LGPL-3.0
#############################################################

import urwid
import serial
from collections import deque
import time
import os
import requests
import random


class Scale:
    """
    Manage the Avery-Berkel 6702-16658 bench scale in ECR mode.
    """

    path_serial = "/dev/ttyAMA0"

    def __init__(self):
        self._weight = 0.0
        self._weight_is_valid = False
        self.ecr_status = None
        self.tare_offset = 0.0

        self.ser = serial.Serial()
        self.ser.port = self.path_serial
        self.ser.baudrate = 9600
        self.ser.timeout = 0.25
        self.ser.parity = serial.PARITY_EVEN
        self.ser.bytesize = serial.SEVENBITS
        self.ser.stopbits = serial.STOPBITS_ONE
        self.ser.xonxoff = False
        self.ser.rtscts = False
        self.ser.dsrdtr = False
        self.ser.open()

    def ecr_set_status(self, response):
        """Parse response and set internal ECR status"""
        assert len(response) == 6
        assert response[0:2] == b"\nS"
        assert response[4:5] == b"\r"
        self.ecr_status = response[2:4]

    def ecr_read(self):
        """Read to ECR EOT (3)"""
        message = bytearray()
        while len(message) == 0 or message[-1] != 3:
            ch = self.ser.read(size=1)
            assert len(ch) == 1  # fail on timeout
            message.append(ch[0])
        return message

    def zero(self):
        """Send ECR Zero command to the scale and read back status"""
        self.ser.reset_input_buffer()
        self.ser.write(b"Z\r")
        response = self.ecr_read()
        self.ecr_set_status(response)

    def poll(self):
        """
        Send ECR Weigh command to the scale and read back either
        weight + status, or just status.  If a valid weight is returned,
        set _weight_is_valid True and convert pounds to grams.
        """
        self.ser.reset_input_buffer()
        self.ser.write(b"W\r")
        response = self.ecr_read()
        if len(response) == 16:
            assert response[0:1] == b"\n"
            assert response[7:10] == b"LB\r"
            self._weight = float(response[1:7]) * 453.592
            self.ecr_set_status(response[10:16])
            self._weight_is_valid = True
        else:
            self.ecr_set_status(response)
            self._weight_is_valid = False

    def tare(self):
        """Incorporate weight of container on scale into future measurements"""
        self.tare_offset = self._weight

    @property
    def at_zero(self):
        """
        Test if scale status indicates scale is at zero.  The zero LED
        on the scale will be lit in this case.
        """
        if self.ecr_status == b"20":
            return True
        return False

    @property
    def display(self):
        """
        Get formatted text for scale readout.
        Gray out previous value if scale is in motion.
        Show red over/under on scale range error.
        """
        if self._weight_is_valid:
            return ("green", "{:.0f}g".format(self._weight - self.tare_offset))
        elif self.ecr_status == b"10" or self.ecr_status == b"30":  # moving
            return ("deselect", "{:.0f}g".format(self._weight - self.tare_offset))
        elif self.ecr_status == b"01" or self.ecr_status == b"11":
            return ("red", "under")
        elif self.ecr_status == b"02":
            return ("red", "over")
        else:
            return ("red", "status:" + self.ecr_status.decode("utf-8"))

    @property
    def weight_is_valid(self):
        """Return True if most recent poll() returned a valid weight."""
        return self._weight_is_valid

    @property
    def weight(self):
        """Return most recently measured weight, less tare offset if any."""
        return self._weight - self.tare_offset


# For testing UI without scale present
class NoScale(Scale):
    """
    Dummy version of scale class for UI testing.
    """

    def __init__(self):
        self._weight = 0.0
        self._weight_is_valid = True
        self.ecr_status = None
        self.tare_offset = 0.0
        return

    def poll(self):
        return

    def zero(self):
        return

    @property
    def display(self):
        return ("deselect", "no scale")


class Progress_mL(urwid.ProgressBar):
    """
    Progress bar that displays mL value instead of percentage.
    It assumes range was set to (0, max capacity in mL).
    """

    def get_text(self):
        return "{:.0f} mL".format(self.current)


class DisplayHelper:

    """
    Urwid color palette.
    Tuples of (Key, font color, background color)
    """

    palette = [
        ("background", "dark blue", ""),
        ("deselect", "dark gray", ""),
        ("select", "dark green", ""),
        ("green", "dark green", ""),
        ("red", "dark red", ""),
        ("pb_todo", "black", "dark red"),
        ("pb_done", "black", "dark green"),
    ]
    """
    Source: https://www.asciiart.eu/food-and-drinks/coffee-and-tea
    N.B. this one had no attribution on that site except author's initials,
    and it seems to be widely disseminated.  Public domain?
    """
    coffee_cup = u'''\
                      (
                        )     (
                 ___...(-------)-....___
             .-""       )    (          ""-.
       .-'``'|-._             )         _.-|
      /  .--.|   `""---...........---""`   |
     /  /    |                             |
     |  |    |                             |
      \  \   |                             |
       `\ `\ |                             |
         `\ `|                             |
         _/ /\                             /
        (__/  \                           /
     _..---""` \                         /`""---.._
  .-'           \                       /          '-.
 :               `-.__             __.-'              :
 :                  ) ""---...---"" (                 :
  '._               `"--...___...--"`              _.'
jgs \""--..__                              __..--""/
     '._     """----.....______.....----"""     _.'
        `""--..,,_____            _____,,..--""`
                      `"""----"""`
'''

    def __init__(self, pot_capacity_mL=100):
        # header
        headL = urwid.Text(("green", "B R E W C O P"), align="left")
        self._headC = urwid.Text("", align="center")
        self._headR = indicator = urwid.Text("", align="right")
        self.header = urwid.Columns([headL, self._headC, self._headR], 3)

        # body
        bg = urwid.Text(self.coffee_cup)
        bg = urwid.AttrMap(bg, "background")
        bg = urwid.Padding(bg, align="center", width=56)
        self.background = urwid.Filler(bg)

        # body + meter pop-up (offline mode)"""
        self._meter = urwid.BigText("", urwid.Thin6x6Font())
        m = urwid.AttrMap(self._meter, "green")
        m = urwid.Padding(m, align="center", width="clip")
        m = urwid.Filler(m, "bottom", None, 7)
        m = urwid.LineBox(m)
        self.meterbody = urwid.Overlay(m, self.background, "center", 50, "middle", 8)

        # footer
        self.pbar = Progress_mL("pb_todo", "pb_done", 0, pot_capacity_mL)
        self.footmsg = urwid.Text(
            ("red", "Brewcop is offline. Replace pot to continue monitoring."),
            align="center",
        )

        self.layout = urwid.Frame(
            header=self.header, body=self.meterbody, footer=self.footmsg
        )

        self.main_loop = urwid.MainLoop(
            self.layout, self.palette, unhandled_input=self.handle_input
        )

    def handle_input(self, key):
        """
        urwid's event loop calls this function on keyboard events
        not handled by widgets.
        """
        if key == "Q" or key == "q":
            raise urwid.ExitMainLoop()

    def tick_wrap(self, _loop, _data):
        """
        urwid timer callback to run registered "tick" function periodically.
        """
        self.ticker()
        _loop.set_alarm_in(self.tick_period, self.tick_wrap)

    def run(self, ticker, tick_period):
        """
        Register ticker callable, to run every tick_period seconds.
        Start urwid's main loop.
        This method does not return until loop exits (press q).
        """
        self.ticker = ticker
        self.tick_period = tick_period
        self.main_loop.set_alarm_in(0, self.tick_wrap)
        self.main_loop.run()

    def redraw(self):
        """
        Force screen redraw.
        It normally redraws when control returns to event loop.
        """
        self.main_loop.draw_screen()

    @property
    def headC(self):
        """Get text from header, center region"""
        return self._headC.get_text()

    @headC.setter
    def headC(self, value):
        """Set text in header, center region"""
        self._headC.set_text(value)

    @property
    def headR(self):
        """Get text from header, right region"""
        return self._headR.get_text()

    @headR.setter
    def headR(self, value):
        """Set text in header, right region"""
        self._headR.set_text(value)

    @property
    def meter(self):
        """Get the meter text (scale reading)"""
        return self._meter.get_text()

    @meter.setter
    def meter(self, value):
        """Set the meter text (scale reading)"""
        self._meter.set_text(value)

    def online(self):
        """Set online display mode (show background + footer progress bar)"""
        self.layout.body = self.background
        self.layout.footer = self.pbar

    def offline(self):
        """Set offline display mode (show meter + footer message)"""
        self.layout.body = self.meterbody
        self.layout.footer = self.footmsg

    def progress(self, value):
        """Update progress bar value (pot contents in mL)"""
        self.pbar.set_completion(value)


class Brains:
    """
    Add some scale memory and semantics for interpreting a series
    of weights as human activity.

    Implement a state machine consisting of the following states:
    unknown - no scale readings stored yet
    brewing - scale readings have shown some increase over last 30s
    ready - scale readings are stable/decreasing and pot still has content
    empty - scale readings are stable/decreasing and pot content is low
    """

    """Retain scale samples for history_length seconds"""
    history_length = 30

    def __init__(self, tick_period=1, empty_thresh=0, stale_thresh=60 * 60 * 8):
        self.history = deque(maxlen=int(self.history_length / tick_period))
        self.pot_empty_thresh_g = empty_thresh
        self.stale_thresh = stale_thresh
        self.state = "unknown"
        self.timestamp = 0

    def ready_message (self, grams):
        template = random.choice([
            "{:.1f}mL of hot, fresh coffee is ready for consumption in B451.",
            "Please drink the {:.1f}mL of coffee in B451.  You have 20 seconds to comply.",
            "You must consume the {:.1f}mL of coffee or be terminated.",
            "{:.1f}mL of human productivity beverage is available for consumption in B451",
            "{:.1f}mL of nootropic brown liquid is available for consumption.",
        ])
        return template.format(grams)

    def notify(self):
        """Notify slack"""
        url = os.environ["SLACK_WEBHOOK_URL"]
        data = {'text' : self.ready_message(self.history[0])}
        requests.post(url, json=data)

    def increasing(self):
        """
        Return true if history shows (any) values increasing relative
        to a predecessor.
        N.B. Ignores l[i] < l[i + 1].
        """
        l = list(self.history)
        return any(x > y for x, y in zip(l, l[1:]))

    def brewcheck(self):
        """
        Process new scale reading, transitioning state, if needed.
        Call notify() on brewing->ready state transition.
        """
        previous = self.state
        if self.increasing():
            if self.state != "brewing":
                self.state = "brewing"
                self.timestamp = time.time()
        elif self.history[0] <= self.pot_empty_thresh_g:
            if self.state != "empty":
                self.state = "empty"
                self.timestamp = time.time()
        else:
            if self.state != "ready":
                self.state = "ready"
                self.timestamp = time.time()
                if previous == "brewing":
                    self.notify()

    def store(self, w):
        """Record a scale measurement"""
        self.history.appendleft(w)
        self.brewcheck()

    def timestr(self, t):
        """Return a human-friendly string representing elapsed time t"""
        daysecs = 60 * 60 * 24
        if t < daysecs:
            return time.strftime("%H:%M:%S", time.gmtime(t))
        elif t < daysecs * 2:
            return "1 day"
        else:
            return "{} days".format(int(t / daysecs))

    @property
    def display(self):
        """Get message text describing the state, with time since entered"""
        t = time.time() - self.timestamp
        timestr = self.timestr(t)
        if self.state == "brewing":
            return ("red", "Brewing, elapsed: {}".format(timestr))
        elif self.state == "ready" and t < self.stale_thresh:
            return ("green", "Ready, elapsed: {}".format(timestr))
        elif self.state == "ready":
            return ("red", "Ready, elapsed: {} (stale)".format(timestr))
        elif self.state == "empty":
            return ("red", "Emptyish, elapsed: {}".format(timestr))
        else:
            return ""


class Brewcop:
    """
    Main Brewcop class.
    """

    tick_period = 0.5

    """Values for Technivorm Moccamaster insulated carafe"""
    pot_tare_g = 795
    pot_capacity_g = 1250  # 1g per mL H20
    pot_empty_thresh_g = 50

    """Declare coffee stale after 4h"""
    stale_thresh = 60 * 60 * 4

    def __init__(self):
        try:
            self.scale = Scale()
        except:
            self.scale = NoScale()
        self.disp = DisplayHelper(pot_capacity_mL=self.pot_capacity_g)
        self.brains = Brains(
            tick_period=self.tick_period,
            empty_thresh=self.pot_empty_thresh_g,
            stale_thresh=self.stale_thresh,
        )
        self._online = False

    @property
    def online(self):
        """Get online status (True or False)"""
        return self._online

    @online.setter
    def online(self, value):
        """
        Set online status (True or False).
        If online, hide the meter and show coffee progress bar.
        If offline, show the meter and replace progress bar with offline msg.
        """
        if self._online and not value:
            self._online = False
            self.disp.offline()
        elif not self._online and value:
            self._online = True
            self.disp.online()

    def poll_scale(self):
        """
        Poll the current scale value.
        Pulse the indicator green so we get visual feedback if this is slow.
        The urwid event loop is stalled while this is happening.
        If it fails, leave the indicator red and set the meter value to ----.
        """
        self.disp.headR = ("green", "poll")
        self.disp.redraw()
        try:
            self.scale.poll()
        except:
            self.disp.headR = ("red", "poll")
            self.disp.meter = "----"
        else:
            self.disp.headR = ""
            self.disp.meter = self.scale.display

    def tick(self):
        """
        urwid's event loop calls this function on tick_period intervals.
        Read the scale, then update the meter and the progress bar.
        Switch online mode depending on weight reading.
        """
        self.poll_scale()
        if self.scale.weight_is_valid:
            w = self.scale.weight - self.pot_tare_g
            if (w > -4 and w < 4): # +/-4g for variation in actual pot tare
                w = 0
            if w < 0:
                self.online = False
            else:
                self.disp.progress(w)
                self.online = True
                self.brains.store(w)
        self.disp.headC = self.brains.display

    def run(self):
        """Enter urwid's event loop.  Start ticker and handle input"""
        self.disp.run(self.tick, self.tick_period)


brewcop = Brewcop()
brewcop.run()

# vim: tabstop=4 shiftwidth=4 expandtab
