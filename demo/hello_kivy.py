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
Kivy "hello world" bring-up demo for the brewcop touchscreen.

This is throwaway scaffolding used to verify, on a freshly flashed
Raspberry Pi OS Lite install, that:

  1. Kivy comes up on the DSI touchscreen (SDL2 on DRM/KMS, no desktop).
  2. Touch input registers and is reported with coordinates.
  3. Display orientation looks right.

It is intentionally self-contained and depends only on python3-kivy.

Run on the Pi console (no X/Wayland) once python3-kivy is installed:

    python3 hello_kivy.py

On a development desktop it opens in a normal window instead; click with
the mouse to exercise the same touch path.  Press 'q' or Escape to quit.
"""

import kivy

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.core.window import Window
from kivy.base import EventLoop

kivy.require("2.1.0")  # oldest release we target (Bookworm ships 2.1.0)


class HelloRoot(BoxLayout):
    """Vertical stack: a title, a live touch readout, and a tap target."""

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=24, spacing=24, **kwargs)

        self.add_widget(
            Label(
                text="B R E W C O P",
                font_size="48sp",
                color=(0.3, 0.8, 0.3, 1),
                size_hint_y=None,
                height="80dp",
            )
        )

        # Live readout of the most recent touch, updated on every event.
        self.readout = Label(
            text="Tap the screen to test touch input",
            font_size="24sp",
        )
        self.add_widget(self.readout)

        # A big obvious tap target that also counts presses, so it is clear
        # touches are landing on widgets and not just the background.
        self.count = 0
        self.button = Button(text="Tap me: 0", font_size="32sp")
        self.button.bind(on_release=self._on_button)
        self.add_widget(self.button)

    def _on_button(self, _button):
        self.count += 1
        self.button.text = "Tap me: {}".format(self.count)

    def on_touch_down(self, touch):
        # Report raw touch coordinates and how many simultaneous touches the
        # driver is delivering (multi-touch sanity check).
        active = len(EventLoop.touches)
        self.readout.text = "touch down @ ({:.0f}, {:.0f})   active: {}".format(
            touch.x, touch.y, active
        )
        return super().on_touch_down(touch)


class HelloApp(App):
    title = "brewcop kivy hello"

    def build(self):
        # Window is None in headless environments with no display provider
        # (e.g. a CI/container box); on the Pi touchscreen it always exists.
        if Window is not None:
            Window.bind(on_key_down=self._on_key_down)
        return HelloRoot()

    def _on_key_down(self, _window, key, _scancode, _codepoint, _modifiers):
        # q (113) or Escape (27) quits, matching the old urwid 'q' behavior.
        if key in (113, 27):
            self.stop()
            return True
        return False


if __name__ == "__main__":
    HelloApp().run()

# vim: tabstop=4 shiftwidth=4 expandtab
