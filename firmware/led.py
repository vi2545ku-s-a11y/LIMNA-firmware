"""
LIMNA firmware - the RGB LED on the board.

The Waveshare board's LED is a single NeoPixel on GPIO 38 (not 48, as on some
other ESP32-S3 boards), and it takes its colours with RED AND GREEN SWAPPED:
sending (40, 0, 0) lights it green. Both found by trying, 2026-11-23. The
swap is handled here, once, so that everything else can say "red" and mean
red.

For now it only cycles red, green and blue while the board waits, so you can
see that the program is running. Turn it off with "led": false in
config.json -- on the buoy every milliamp counts.
"""

import time

RED = (40, 0, 0)
GREEN = (0, 40, 0)
BLUE = (0, 0, 40)
OFF = (0, 0, 0)


class Led:
    def __init__(self, pin=38, enabled=True):
        self.np = None
        if enabled:
            try:
                import machine
                import neopixel
                self.np = neopixel.NeoPixel(machine.Pin(pin), 1)
            except Exception as exc:
                print("  [lysdiod]  gar inte att starta:", exc)

    def show(self, rgb):
        if self.np is None:
            return
        r, g, b = rgb
        self.np[0] = (g, r, b)        # this LED wants green first
        self.np.write()

    def cycle(self, seconds, colours=(RED, GREEN, BLUE), step_s=1):
        """Wait `seconds`, switching colour every `step_s`. Off afterwards."""
        if self.np is None:
            time.sleep(seconds)
            return
        end = time.time() + seconds
        i = 0
        while time.time() < end:
            self.show(colours[i % len(colours)])
            i += 1
            time.sleep(min(step_s, max(0, end - time.time())))
        self.show(OFF)
