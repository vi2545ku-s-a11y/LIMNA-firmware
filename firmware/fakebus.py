"""
LIMNA firmware - simulated sensors on the board, until the RS485 module comes.

FakeBus has the same read() as the real RTUBus, so main.py cannot tell them
apart. Which one is used is "bus" in config.json: "fake" or "rtu".

The registers come from the shared profile, so a raw value here is encoded
exactly as the real sensor would send it -- pH x 100, ORP x 10 as a signed
16-bit number, turbidity x 10 -- and main.py decodes it the same way it will
decode the real thing.

The water is a cut-down copy of the pond in laptop/fake_sensors.py: pH and ORP
follow daylight, turbidity rises while the farmer pumps in the morning. The
warm-up behaviour is kept, because it is the point of the simulation: read
too soon after power_on() and the value is wrong while looking plausible.
"""

import math
import random
import time


def _uniform():
    # random.random() is optional in MicroPython; getrandbits() is always there.
    return random.getrandbits(30) / 1073741824


def _gauss(sigma):
    # Box-Muller, since MicroPython has no random.gauss().
    u1 = max(_uniform(), 1e-9)
    return sigma * math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * _uniform())


def _encode(value, scale, signed):
    raw = int(round(value * scale))
    if signed:
        return max(-32768, min(32767, raw)) & 0xFFFF
    return max(0, min(65535, raw))


class FakeBus:
    def __init__(self, profile, specs, temp_specs, utc_offset_h=1,
                 clock=time.time):
        self.clock = clock
        self.utc_offset_h = utc_offset_h
        self.powered_at = None
        self.warmup = {s["unit"]: s["warmup_s"] for s in profile["sensors"]}
        self.offset_c = {"ph": 0.15, "turb": -0.10}    # as in fake_sensors.py
        self.values = {}
        for spec in specs:
            self.values.setdefault(spec["unit"], []).append(spec)
        self.temps = {spec["unit"]: spec for spec in temp_specs}

    def power_on(self):
        self.powered_at = self.clock()
        print("  [strom pa] sensorrail (simulerad)")

    def power_off(self):
        self.powered_at = None
        print("  [strom av] sensorrail (simulerad)")

    # -- the pond ----------------------------------------------------------
    def _daylight(self, now):
        h = ((now / 3600.0) + self.utc_offset_h) % 24
        return math.sin(2 * math.pi * (h - 10.0) / 24.0), h

    def _true(self, key, now):
        day, h = self._daylight(now)
        if key == "ph":
            return 7.6 + 0.35 * day
        if key == "orp":
            return 260.0 + 25.0 * day
        if key == "turb":
            pumping = 1.0 if 5 <= h < 8 else 0.0
            return 6.0 + 4.0 * pumping
        if key == "temp":
            return 18.4 + 0.8 * math.sin(2 * math.pi * (h - 9.0) / 24.0)
        raise ValueError("okand storhet " + key)

    def _settle(self, unit, now):
        if self.powered_at is None:
            return 0.0                    # unpowered: as wrong as it gets
        warm = self.warmup.get(unit, 1.0)
        return 1.0 if warm <= 0 else min(1.0, (now - self.powered_at) / warm)

    # -- the same interface as RTUBus --------------------------------------
    def read(self, unit, register, count=1):
        if self.powered_at is None:
            raise OSError("svarar inte (railen ar av)")
        now = self.clock()
        f = self._settle(unit, now)
        regs = {}
        for spec in self.values.get(unit, []):
            value = self._true(spec["key"], now) + _gauss(0.002 * abs(spec["scale"]) / 10)
            if f < 1.0:
                # The same distortion as fake_sensors.py: reads low, wanders.
                value = value * (0.55 + 0.45 * f) + _gauss((1 - f) * 0.6)
            regs[spec["addr"]] = _encode(value, spec["scale"], spec["signed"])
        t = self.temps.get(unit)
        if t is not None:
            value = self._true("temp", now) + self.offset_c.get(t["sensor"], 0.0)
            regs[t["addr"]] = _encode(value, t["scale"], t["signed"])
        if not regs:
            raise OSError("svarar inte (ingen givare pa adress %d)" % unit)
        return [regs.get(register + i, 0) for i in range(count)]
