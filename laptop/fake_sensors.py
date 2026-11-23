#!/usr/bin/env python3
"""
LIMNA - simulated Modbus sensors.

Pretends to be the RS485/Modbus sensors of the buoy so the whole software
chain can be built and tested before any hardware arrives.

Runs a Modbus TCP server. Switching to Modbus RTU over a real serial port
later changes ONE line in poller.py and nothing else -- the register layout
and the polling logic are identical.

WHICH sensors, on which addresses, with which registers, comes from
sensor_profiles.py. This file holds no register numbers of its own; it holds
the water chemistry and the faults. The poller reads the same profile, and
sensor_profiles.py --selftest checks that the two still agree.

The pond profile has two sensors: the Seeed pH/ORP probe on unit 1, which
reports pH, ORP and temperature, and the Seeed turbidity probe on unit 2,
which reports turbidity and temperature.

Fault injection: edit faults.json while this is running. It is re-read on
every request, so you can kill a sensor mid-run and watch the poller cope.

    python3 fake_sensors.py              normal, units from the profile
    python3 fake_sensors.py --factory    units answer on their FACTORY
                                         addresses, including the collision
    python3 fake_sensors.py --selftest   water model only, no pymodbus needed

Requires:  pip install pymodbus
"""

import json
import math
import os
import random
import time

import sys

# sensor_profiles.py ligger i delad/, en niva upp, eftersom firmwaren pa
# ESP32:n ska lasa exakt samma registerkarta. Tre rader har i stallet for en
# kopia per mapp -- en kopia ar precis det felet profilen finns for att
# forhindra.
DELAD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     os.pardir, "delad")
if DELAD not in sys.path:
    sys.path.insert(0, DELAD)

import sensor_profiles

# The simulator must read the same config as the poller, or the two could end
# up rehearsing different sensor families.
sensor_profiles.use_config(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

# The self test must run without pymodbus installed (README stage 1), so this
# import is allowed to fail. main() checks properly before starting a server.
try:
    from pymodbus.datastore import ModbusSlaveContext
except ImportError:
    ModbusSlaveContext = object

FAULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faults.json")

# ---------------------------------------------------------------------------
# REGISTER LAYOUT  -- read from the profile, not written down here
# ---------------------------------------------------------------------------
# Modbus sensors return integers, not floats. A reading of 7.83 pH is sent as
# 783 with an implied scale factor of 100; 239.5 mV ORP as 2395 with a scale
# of 10.
#
# The numbers themselves live in sensor_profiles.py, in one place, because
# they belong to a sensor FAMILY and not to this simulator. Everything below
# is a view of that table. Change a register there and both this file and the
# poller follow; write one in by hand here and sensor_profiles.py --selftest
# fails loudly, which is the whole point of it.
PROFILE = sensor_profiles.load_profile()

#   payload key : (unit, address, scale, signed)
REGISTERS = sensor_profiles.value_registers(PROFILE)

# The same map grouped by bus address: one sensor can answer for several
# values. The pH probe on unit 1 holds both pH and ORP.
VALUE_SPECS = {}
for _spec in sensor_profiles.value_specs(PROFILE):
    VALUE_SPECS.setdefault(_spec["unit"], []).append(_spec)

SENSOR_ID = {s["unit"]: s["id"] for s in sensor_profiles.sensors(PROFILE)}
SENSOR_NAME = {s["unit"]: s["label"] for s in sensor_profiles.sensors(PROFILE)}

# Sensors that also report water temperature. In the pond profile both do;
# the profile says which one fills the payload's temp field.
#
#   unit : (address, scale, signed)
TEMP_REGISTERS = sensor_profiles.temp_registers(PROFILE)
TEMP_SOURCE_UNIT = sensor_profiles.temp_primary_unit(PROFILE)

# A thermometer has its own calibration error, a few tenths of a degree,
# fixed for the life of the sensor. Keyed by sensor id so it follows the
# sensor if addresses move. The two are deliberately different: two
# identical simulated thermometers would make temp_d always 0.0, and the
# poller's cross-check could never be seen working. Both offsets are inside
# the datasheets' +-0.5 and +-0.3 C.
TEMP_OFFSET_C = {"ph": 0.15, "turb": -0.10}

# A drifting sensor board takes its thermometer with it -- an ageing reference
# voltage or ADC shifts every channel, not just the interesting one. That is
# the failure the poller's temp_d comparison catches, so "drift" moves the
# temperature too, more slowly than the main value.
#
# Not for every sensor. Fouling on the turbidity probe's optical window is
# the drift that matters there, and a biofilm on the glass does nothing to a
# Pt1000 inside the body.
TEMP_DRIFT_PER_HOUR = 0.08
DRIFT_MOVES_TEMP = {"ph": True, "turb": False}

# Where a datasheet states the measuring range, the sensor cannot report
# outside it -- it saturates at the end of its scale instead. Without this a
# drifting pH probe would happily report pH 15, which no real device can do
# and which would make the drift fault look like a bug in the model.
#
#   payload key : (lowest raw value, highest raw value)
REGISTER_LIMITS = sensor_profiles.raw_limits(PROFILE)


def to_register(value, scale, signed, key=None):
    """Encode a float the way a real sensor would, as one 16-bit register.

    Signed values are sent as two's complement, which is why reading them
    back as unsigned turns -12.5 mV into 6541.1 mV.
    """
    raw = int(round(value * scale))
    if key in REGISTER_LIMITS:
        low, high = REGISTER_LIMITS[key]
        raw = max(low, min(high, raw))
    if signed:
        raw = max(-32768, min(32767, raw))
        return raw & 0xFFFF
    return max(0, min(65535, raw))


# ---------------------------------------------------------------------------
# FACTORY ADDRESSES  -- and the collision waiting on the bench
# ---------------------------------------------------------------------------
# Every sensor ships with a factory address that has to be changed before the
# buoy can work. Which register that is written to differs between sensors:
# 0x0200 on the pH probe, 0x2000 on the turbidity probe.
#
# BOTH SENSORS SHIP ON ADDRESS 1 -- going by the turbidity manual's examples;
# its register table says 50, see the profile.
#
# Wire both in before re-addressing and they answer simultaneously on the same
# pair of wires. The result is not two replies but one unreadable one -- the
# waveforms overlap and the frame fails its checksum. What the control board
# reports is "no response", which sends you looking for a broken sensor or a
# loose wire instead of an address clash.
#
# Change one of them BEFORE connecting the other.
FACTORY_ADDRESSES = sensor_profiles.factory_addresses(PROFILE)
ADDRESS_REGISTERS = sensor_profiles.address_registers(PROFILE)


# ---------------------------------------------------------------------------
# WATER CHEMISTRY MODEL
# ---------------------------------------------------------------------------
# The point of modelling this properly rather than emitting random numbers is
# that the alarm rules can then be tested against behaviour that looks like a
# real irrigation pond:
#
#   pH      rises in daylight as algae and plants take up CO2, falls at night.
#   ORP     follows the same day, more weakly. Low or negative is water
#           without oxygen.
#   turbidity   low and calm, until the farmer pumps in the morning and stirs
#           up the bottom, or rain washes soil in from the fields.
#
# None of the numbers are measured. They are chosen to look like a pond.

class Pond:
    """A farm irrigation pond in Skane during the season, April to October."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.t0 = time.time()
        # Slow random walk so values drift instead of jumping
        self._walk = {"ph": 0.0, "orp": 0.0, "turb": 0.0, "temp": 0.0}
        # Rain event state
        self.rain_started_at = None

    # -- helpers ------------------------------------------------------------
    def _hour_of_day(self, now):
        lt = time.localtime(now)
        return lt.tm_hour + lt.tm_min / 60.0 + lt.tm_sec / 3600.0

    def _drift(self, key, step, limit):
        """Bounded random walk. Keeps values wandering, not jittering."""
        self._walk[key] += self.rng.gauss(0, step)
        self._walk[key] = max(-limit, min(limit, self._walk[key]))
        return self._walk[key]

    def _rain_factor(self, now):
        """0.0 when dry, rises fast then decays over about six hours."""
        if self.rain_started_at is None:
            return 0.0
        age_h = (now - self.rain_started_at) / 3600.0
        if age_h < 0:
            return 0.0
        # Fast rise over ~20 min, exponential decay after
        rise = min(1.0, age_h / 0.33)
        decay = math.exp(-max(0.0, age_h - 0.33) / 2.0)
        return rise * decay

    def _daylight(self, now):
        """-1 just before dawn, +1 in the late afternoon."""
        h = self._hour_of_day(now)
        return math.sin(2 * math.pi * (h - 10.0) / 24.0)

    def _pumping(self, now):
        """0-1, how hard the irrigation pump is stirring the pond.

        The farmer irrigates early, 05 to 08. The intake pulls water past the
        bottom and lifts sediment, which settles again over a couple of hours.
        """
        h = self._hour_of_day(now)
        if 5 <= h < 8:
            return 1.0
        if 8 <= h < 10:
            return 1.0 - (h - 8) / 2
        return 0.0

    def _bloom_hours(self, now, bloom):
        return (now - self.t0) / 3600.0 if bloom else 0.0

    # -- quantities ---------------------------------------------------------
    def temperature(self, now):
        """Water temperature. Small diurnal swing, lags the air by ~3 hours."""
        h = self._hour_of_day(now)
        diurnal = 0.8 * math.sin(2 * math.pi * (h - 9.0) / 24.0)
        return 18.4 + diurnal + self._drift("temp", 0.02, 0.5)

    def ph(self, now, bloom=False):
        """
        Diurnal cycle: photosynthesis takes CO2 out of the water in daylight
        and pH climbs; respiration puts it back at night. A swing of most of
        a pH unit is ordinary in a nutrient-rich pond. Rain is softer and
        slightly acidic, and pulls it down for a few hours.

        A bloom shows up as the afternoon peak climbing day by day, towards
        9 and beyond -- which is where irrigation water starts to scale
        drippers and lose its effect on some sprays.
        """
        value = (7.6 + 0.35 * self._daylight(now)
                 + self._drift("ph", 0.005, 0.1)
                 - 0.25 * self._rain_factor(now)
                 + 0.25 * self._bloom_hours(now, bloom))
        return max(0.0, min(14.0, value))

    def orp(self, now, bloom=False):
        """Oxidation-reduction potential in mV.

        A few hundred millivolts in oxygenated surface water, a little higher
        in the afternoon. A bloom that collapses at night uses up the oxygen
        and drags ORP down, towards zero and below.
        """
        return (260.0 + 25.0 * self._daylight(now)
                + self._drift("orp", 0.5, 15.0)
                - 10.0 * self._bloom_hours(now, bloom))

    def turbidity(self, now, bloom=False):
        """Turbidity in NTU.

        Single figures when the pond is calm. The morning pumping stirs up
        the bottom, and rain brings soil in from the fields -- by far the
        largest swing, tens of NTU. A bloom turns the water green and cloudy.
        """
        value = (6.0 + self._drift("turb", 0.05, 1.5)
                 + 4.0 * self._pumping(now)
                 + 45.0 * self._rain_factor(now)
                 + 6.0 * self._bloom_hours(now, bloom))
        return max(0.0, value)


# ---------------------------------------------------------------------------
# FAULT INJECTION
# ---------------------------------------------------------------------------

# One line per sensor in the profile, named after its address and its id, so
# a new sensor or a swapped family brings its own line along. A fault is a
# property of the DEVICE: an offline pH probe takes pH and ORP with it.
FAULT_KEYS = {unit: f"sensor_{unit}_{sid}" for unit, sid in SENSOR_ID.items()}


def default_faults():
    """The faults.json written on first run, built from the profile."""
    faults = {"_comment": "Edit while running. Re-read on every request."}
    for unit in sorted(FAULT_KEYS):
        faults[FAULT_KEYS[unit]] = "ok"
    faults["_states"] = "ok | offline | stuck | noisy | drift"
    faults["rain_event"] = False
    # Simulated algal bloom: pH and turbidity climb, ORP falls, hour by hour.
    # Used for alarm testing.
    faults["bloom"] = False
    return faults


DEFAULT_FAULTS = default_faults()

# Value a "stuck" sensor freezes at. Deliberately unlike any real reading so
# it is recognisable in a log. Keyed by payload key: this is behaviour of the
# simulated quantity, not of the bus address it happens to sit on.
STUCK_VALUES = {"ph": 8.88, "orp": 222.2, "turb": 22.2}

# "drift" moves the reading by this much per hour. The sensor keeps answering
# and the value keeps looking plausible -- it is simply wrong, and gets more
# wrong the longer it runs.
#
# For the turbidity probe this is BIOLOGICAL FOULING: a biofilm grows over
# the optical window, scatters light of its own, and the reading creeps UP
# while the sensor answers perfectly. In a warm, nutrient-rich pond in summer
# it is the main maintenance problem. For the pH probe it is the reference
# electrode ageing -- the manual's own list of problems has "drifting
# readings" on it -- and ORP goes with it.
#
# ACCELERATED FOR TESTING, NOT A PREDICTION. Real fouling builds over weeks.
# At +3 NTU per hour a test run shows it in an afternoon. How fast it really
# happens is something the first season has to measure.
DRIFT_PER_HOUR = {"ph": -0.10, "orp": -12.0, "turb": 3.0}


def read_faults():
    try:
        with open(FAULTS_FILE) as fh:
            return json.load(fh)
    except Exception:
        return DEFAULT_FAULTS


def ensure_faults_file():
    """Create faults.json, or add rows for sensors it does not know about.

    Swapping or adding a sensor should not mean hand-editing this file to be
    able to break it. Existing rows are never touched -- a fault you set
    stays set.
    """
    if not os.path.exists(FAULTS_FILE):
        with open(FAULTS_FILE, "w") as fh:
            json.dump(DEFAULT_FAULTS, fh, indent=2)
        return

    faults = read_faults()
    saknas = {key: "ok" for key in FAULT_KEYS.values() if key not in faults}
    if saknas:
        faults.update(saknas)
        with open(FAULTS_FILE, "w") as fh:
            json.dump(faults, fh, indent=2)
        print(f"  [faults]    lade till {', '.join(sorted(saknas))} "
              f"i {os.path.basename(FAULTS_FILE)}")


# ---------------------------------------------------------------------------
# WARM-UP BEHAVIOUR
# ---------------------------------------------------------------------------
# The buoy cuts power to the sensors between measurements, so every reading
# starts from a cold sensor. This is the number the whole energy budget turns
# on, so the simulator makes it visible: read too soon after power-up and you
# get a value that has not stabilised yet.
#
# Where each figure comes from is part of the profile, not of this file. None
# of them is a measured start-up time: the pH probe's 30 s is its datasheet
# RESPONSE time for ORP, and the turbidity probe's 10 s is a guess -- its
# datasheet says nothing at all.
#
# The self test prints this table, so the assumptions stay visible to whoever
# runs the code rather than only to whoever reads this comment.
WARMUP_S = sensor_profiles.warmup_times(PROFILE)
WARMUP_SOURCE = sensor_profiles.warmup_sources(PROFILE)
WARMUP_LABEL = sensor_profiles.warmup_labels(PROFILE)


class WarmupTracker:
    """Remembers when the sensor rail was last switched on.

    The poller opening the Modbus connection IS the rail being powered up,
    and closing it is the rail being cut. That mirrors the firmware exactly:

        power on  ->  wait warmup_s  ->  read  ->  power off

    Because of that, waiting long enough gives a correct reading and reading
    too soon does not -- which is the whole point of the simulation. Working
    it out from the gap between reads instead cannot work: the simulator
    never sees the waiting, only the read that follows it.
    """

    def __init__(self):
        self.powered_on_at = {}

    def power_on(self, now):
        """The sensor rail comes up: every sensor starts warming at once."""
        for unit in WARMUP_S:
            self.powered_on_at[unit] = now

    def settle_fraction(self, unit, now):
        """1.0 = fully settled. Below 1.0 the reading is still drifting in."""
        t_on = self.powered_on_at.get(unit, now)
        warm = WARMUP_S.get(unit, 1.0)
        if warm <= 0:
            return 1.0
        return min(1.0, (now - t_on) / warm)


# ---------------------------------------------------------------------------
# SERVER
# ---------------------------------------------------------------------------

def apply_rain_state(pond, faults, now):
    """Start or clear a rain event, following faults.json."""
    if faults.get("rain_event"):
        if pond.rain_started_at is None:
            pond.rain_started_at = now
            print("  [handelse]  regn startat")
    else:
        pond.rain_started_at = None


def pond_value(pond, key, now, faults):
    """The true value of one measured quantity, before any fault or warm-up.

    Dispatches on the payload key, not on the bus address, so moving a sensor
    to another address -- or swapping the whole family -- changes nothing here.
    """
    bloom = bool(faults.get("bloom", False))
    if key == "ph":
        return pond.ph(now, bloom=bloom)
    if key == "orp":
        return pond.orp(now, bloom=bloom)
    if key == "turb":
        return pond.turbidity(now, bloom=bloom)
    raise SystemExit(
        f"Profilen innehaller matvardet '{key}', men vattenmodellen i "
        f"fake_sensors.py vet inte hur den storheten beter sig.\n"
        f"Lagg till den i pond_value() och i klassen Pond.")


def build_unit(pond, warmup, unit, now, faults):
    """Return {register address: raw_int} for ONE sensor, or None if offline.

    Every value the sensor reports, plus its thermometer, in one go -- the
    way a real device holds them all in one register block.
    """
    sid = SENSOR_ID[unit]
    state = faults.get(FAULT_KEYS[unit], "ok")
    if state == "offline":
        return None                        # unit simply absent

    # How far into its warm-up the sensor is. An unsettled sensor answers
    # with a WRONG NUMBER, drifting in towards the truth -- see below.
    f = warmup.settle_fraction(unit, now)

    regs = {}
    for spec in VALUE_SPECS[unit]:
        key = spec["key"]
        value = pond_value(pond, key, now, faults)

        if state == "stuck":
            value = STUCK_VALUES[key]
        elif state == "noisy":
            value *= (1.0 + pond.rng.gauss(0, 0.25))
        elif state == "drift":
            value += DRIFT_PER_HOUR[key] * ((now - pond.t0) / 3600.0)

        # Apply warm-up: an unsettled sensor reads low and wanders.
        #
        # The noise is drawn from the pond's own generator, not the global
        # random module. Seeding a Pond then reading it through an unseeded
        # global made the self test print different warm-up numbers on every
        # run, which is no use at all for checking that a change to this code
        # left the behaviour intact -- and that check is exactly what this
        # simulation exists for. Pond() without a seed is still
        # unpredictable, so live runs are unchanged.
        if f < 1.0:
            value = value * (0.55 + 0.45 * f) + pond.rng.gauss(0, (1 - f) * 0.6)

        regs[spec["addr"]] = to_register(value, spec["scale"], spec["signed"],
                                         key)

    if unit in TEMP_REGISTERS:
        t_addr, t_scale, t_signed = TEMP_REGISTERS[unit]
        t_value = pond.temperature(now) + TEMP_OFFSET_C.get(sid, 0.0)
        if state == "drift" and DRIFT_MOVES_TEMP.get(sid, True):
            t_value += TEMP_DRIFT_PER_HOUR * ((now - pond.t0) / 3600.0)
        elif state == "stuck":
            t_value = 11.11
        regs[t_addr] = to_register(t_value, t_scale, t_signed)
    return regs


def build_values(pond, warmup, now, faults):
    """Return {unit: {register: raw_int}} for every sensor at once.

    Only the self test uses this. The server builds one unit at a time, at
    the moment that unit is actually read.
    """
    out = {}
    for unit in VALUE_SPECS:
        regs = build_unit(pond, warmup, unit, now, faults)
        if regs is not None:
            out[unit] = regs
    return out


class SensorContext(ModbusSlaveContext):
    """One simulated sensor on the Modbus bus.

    The values are worked out at the moment the poller ACTUALLY reads, not on
    a background timer. Together with WarmupTracker, which starts counting
    when the connection opens, that makes the warm-up simulation honest: a
    sensor read too soon after power-on is still drifting in, and one read
    after a proper wait is not.
    """

    def __init__(self, unit, pond, warmup, bus_address=None, **kwargs):
        super().__init__(**kwargs)
        self.unit = unit
        self.pond = pond
        self.warmup = warmup
        # Which address this sensor currently ANSWERS on, which is not the
        # same thing as which sensor it is. They differ in factory mode, and
        # they differ again the moment set_address.py moves one.
        self.bus_address = unit if bus_address is None else bus_address
        self.server = None            # set by build_server_context()

    def address_register(self):
        return ADDRESS_REGISTERS[self.unit]

    def validate(self, fc_as_hex, address, count=1):
        """An offline sensor does not answer at all."""
        faults = read_faults()
        if faults.get(FAULT_KEYS[self.unit], "ok") == "offline":
            return False
        # The address register sits far above the value registers -- 0x0200
        # and 0x2000 for ours -- so it is outside the block allocated for
        # readings. Accept it explicitly rather than making every sensor
        # carry eight thousand empty registers.
        if address == self.address_register():
            return True
        return super().validate(fc_as_hex, address, count)

    def getValues(self, fc_as_hex, address, count=1):
        if address == self.address_register():
            # What a sensor echoes back after an address write. Reading it is
            # also the only way to ask "who are you?" without guessing.
            return [self.bus_address] * count
        now = time.time()
        faults = read_faults()
        apply_rain_state(self.pond, faults, now)
        regs = build_unit(self.pond, self.warmup, self.unit, now, faults)
        if regs is None:
            return [0] * count
        return [regs.get(address + i, 0) for i in range(count)]

    def setValues(self, fc_as_hex, address, values):
        """Writing the address register MOVES the sensor, as on the bench.

        Without this the simulator would accept the write, store the number
        and go on answering where it always did -- so the rehearsal would
        pass while the one thing being rehearsed did not happen. The sensor
        has to actually disappear from its old address, because that is how
        you tell on real hardware that the write took.

        The real pH probe only moves after a restart. The simulator moves at
        once; set_address.py says what to do on the bench.
        """
        if address != self.address_register():
            return super().setValues(fc_as_hex, address, values)

        ny = int(values[0])
        if not 1 <= ny <= 247:
            # Modbus RTU addresses stop at 247. A real sensor would reject
            # this; so does this one, by ignoring it.
            print(f"  [ADRESS] {SENSOR_NAME[self.unit]} vagrar adress {ny} "
                  f"-- giltiga adresser ar 1-247.")
            return

        gammal = self.bus_address
        if ny == gammal:
            return
        if self.server is not None:
            self.server[ny] = self
            gamla = self.server[gammal]
            if isinstance(gamla, CollisionContext):
                # Moved out of a shared factory address: the sensor still
                # plugged in elsewhere on the bench stays where it was.
                gamla.remove(self)
            else:
                del self.server[gammal]
        self.bus_address = ny
        print(f"  [ADRESS] {SENSOR_NAME[self.unit]} flyttad fran adress "
              f"{gammal} till {ny}. Den svarar inte langre pa {gammal}.")


class CollisionContext(ModbusSlaveContext):
    """Two sensors sharing one address, as they arrive from the factory.

    pymodbus cannot hold two devices on the same unit id, and faking it by
    returning one of them would hide the whole problem. So this stands in for
    the pair and reproduces the SYMPTOM instead of the mechanism.

    On the real bus both sensors start transmitting at once, the waveforms
    overlap, and the frame that arrives fails its checksum. The master does
    not receive two answers; it receives nothing it can use, and reports a
    timeout. That is why this refuses to answer rather than returning
    nonsense values: garbage that parses cleanly would be a LESS accurate
    simulation than silence, because a real master never gets that far.

    The misleading part is the whole point. "Sensor does not respond" sends
    you hunting for a dead sensor or a broken wire, and the actual fault is
    that you wired in two devices that both think they are number 1.

    UNPLUGGING ONE. Set a sensor "offline" in faults.json and it is off the
    bus: if only one is left on the address, that one answers normally.
    That is the bench procedure -- one sensor connected, move it, then the
    next -- and the rehearsal has to allow it.
    """

    def __init__(self, units, contexts=None, **kwargs):
        super().__init__(**kwargs)
        self.units = units
        # One ordinary SensorContext per sensor, used when it is alone.
        self.contexts = contexts or []
        self._warned = False

    def remove(self, context):
        """A sensor has been moved away from this address."""
        self.contexts = [c for c in self.contexts if c is not context]
        self.units = [c.unit for c in self.contexts]

    def _alone(self):
        """The one sensor still connected here, or None if 0 or 2+ are."""
        faults = read_faults()
        kvar = [c for c in self.contexts
                if faults.get(FAULT_KEYS[c.unit], "ok") != "offline"]
        return kvar[0] if len(kvar) == 1 else None

    def getValues(self, fc_as_hex, address, count=1):
        alone = self._alone()
        if alone is not None:
            return alone.getValues(fc_as_hex, address, count)
        return [0] * count

    def setValues(self, fc_as_hex, address, values):
        alone = self._alone()
        if alone is not None:
            return alone.setValues(fc_as_hex, address, values)

    def validate(self, fc_as_hex, address, count=1):
        faults = read_faults()
        kvar = [c for c in self.contexts
                if faults.get(FAULT_KEYS[c.unit], "ok") != "offline"]
        if len(kvar) == 1:
            return kvar[0].validate(fc_as_hex, address, count)
        if not kvar:
            return False
        if not self._warned:
            names = " och ".join(SENSOR_NAME[u] for u in self.units)
            print(f"\n  [KROCK] Tva sensorer ({names}) svarar pa samma adress.")
            print("          Pa den riktiga bussen sander de samtidigt och "
                  "svaret blir olasligt.")
            print("          Mastern ser bara en timeout och rapporterar "
                  "'svarar inte'.")
            print("          Andra adress pa den ena INNAN den andra "
                  "kopplas in.\n")
            self._warned = True
        return False


def build_server_context(pond, warmup, factory=False):
    """Map units onto bus addresses from the profile, or the factory set."""
    from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext

    def block():
        # 0x0101 is the highest value register we answer on, so 0x0110
        # registers is comfortably enough for every sensor.
        return ModbusSequentialDataBlock(0, [0] * 0x0110)

    slaves = {}
    if not factory:
        for unit in VALUE_SPECS:
            slaves[unit] = SensorContext(unit, pond, warmup,
                                         bus_address=unit, hr=block())
    else:
        # Factory mode: group units by the address they ship on, so any
        # address claimed by more than one sensor becomes a collision instead
        # of a device.
        by_address = {}
        for unit, addr in FACTORY_ADDRESSES.items():
            by_address.setdefault(addr, []).append(unit)

        for addr, units in by_address.items():
            if len(units) == 1:
                slaves[addr] = SensorContext(units[0], pond, warmup,
                                             bus_address=addr, hr=block())
            else:
                contexts = [SensorContext(u, pond, warmup, bus_address=addr,
                                          hr=block()) for u in units]
                slaves[addr] = CollisionContext(units, contexts=contexts,
                                                hr=block())

    context = ModbusServerContext(slaves=slaves, single=False)
    # Each sensor needs to reach the collection it sits in, so that writing
    # its address register can move it from one entry to another -- which is
    # what a real sensor does when you re-address it.
    for slave in slaves.values():
        if isinstance(slave, SensorContext):
            slave.server = context
        elif isinstance(slave, CollisionContext):
            for inner in slave.contexts:
                inner.server = context
    return context


def main(factory=False):
    ensure_faults_file()

    try:
        from pymodbus.server import StartTcpServer
    except ImportError:
        raise SystemExit(
            "pymodbus saknas.  Kor:  pip install -r requirements.txt\n"
            "Vill du bara se att vattenmodellen fungerar, kor:  "
            "python3 fake_sensors.py --selftest")

    pond = Pond()
    warmup = WarmupTracker()
    context = build_server_context(pond, warmup, factory=factory)

    def rail_power(connected):
        """The poller opening the bus is the sensor rail being switched on."""
        if connected:
            warmup.power_on(time.time())

    print("LIMNA sensorsimulator")
    print("  Modbus TCP pa  localhost:5020")
    print(f"  Profil:        {PROFILE['id']}  ({PROFILE['namn']})")
    if factory:
        print("  Lage:          FABRIKSADRESSER")
        for unit, addr in sorted(FACTORY_ADDRESSES.items()):
            krock = " <- KROCKAR" if list(
                FACTORY_ADDRESSES.values()).count(addr) > 1 else ""
            print(f"                 adress {addr:>3}  "
                  f"{SENSOR_NAME[unit]}  andras via "
                  f"0x{ADDRESS_REGISTERS[unit]:04X}{krock}")
        print(f"  Adressregistren ar {PROFILE['adressregister_status']} "
              f"-- kontrollera mot databladet innan monteringsdagen.")
    else:
        enheter = "  ".join(f"{s['unit']}={s['kort']}"
                            for s in sensor_profiles.sensors(PROFILE))
        print(f"  Enheter:       {enheter}")
    print(f"  Felinjektion:  redigera {os.path.basename(FAULTS_FILE)} under drift")
    print("  Avsluta med Ctrl+C\n")
    StartTcpServer(context=context, address=("127.0.0.1", 5020),
                   trace_connect=rail_power)


# ---------------------------------------------------------------------------
# SELF TEST -- runs without pymodbus, so the water model can be checked first
# ---------------------------------------------------------------------------

def _decode(raw, scale, signed):
    """Mirror of poller.decode_register(), kept here so the self test can
    check the round trip. Deliberately duplicated rather than imported: the
    poller must not depend on the simulator, since on real hardware the
    simulator is not installed."""
    if signed and raw >= 0x8000:
        raw -= 0x10000
    return raw / scale


def _read(vals, key):
    """Decode one value out of build_values() output, as the poller would."""
    unit, addr, scale, signed = REGISTERS[key]
    return _decode(vals[unit][addr], scale, signed)


def selftest():
    print("Vattenmodell, ett dygn i steg om tva timmar\n")
    pond = Pond(seed=1)
    base = time.mktime((2026, 7, 15, 0, 0, 0, 0, 0, -1))

    print(f"{'kl':>5} {'temp':>7} {'pH':>6} {'ORP':>7} {'grumlig':>8}")
    for step in range(0, 24, 2):
        now = base + step * 3600
        print(f"{time.strftime('%H:%M', time.localtime(now)):>5} "
              f"{pond.temperature(now):7.2f} "
              f"{pond.ph(now):6.2f} "
              f"{pond.orp(now):7.1f} "
              f"{pond.turbidity(now):8.1f}")
    print("\n  pH stiger med dagsljuset och sjunker pa natten. Grumligheten")
    print("  okar 05-08 nar pumpen ror upp botten och sjunker undan efterat.")

    print("\nRegnhandelse, timme for timme fran start\n")
    pond2 = Pond(seed=2)
    pond2.rain_started_at = base + 12 * 3600
    print(f"{'h':>4} {'grumlig':>8} {'pH':>6}")
    for h in [0, 0.25, 0.5, 1, 2, 3, 4, 6, 8]:
        now = pond2.rain_started_at + h * 3600
        print(f"{h:4} {pond2.turbidity(now):8.1f} {pond2.ph(now):6.2f}")

    # -- warm-up, the slow sensor against the fast one ----------------------
    slow = max(VALUE_SPECS, key=lambda u: WARMUP_S[u])
    fast = min(VALUE_SPECS, key=lambda u: WARMUP_S[u])
    print(f"\nUppvarmning: {WARMUP_LABEL[slow]} tar {WARMUP_S[slow]:g} s, "
          f"{WARMUP_LABEL[fast]} ar klar pa {WARMUP_S[fast]:g} s\n")
    pond3 = Pond(seed=3)
    w = WarmupTracker()
    # A fixed instant rather than time.time(), so this table is byte for byte
    # the same on every run. Together with the seeded pond that turns it into
    # a regression check: change anything in the warm-up code and whatever
    # moves here is the change, not the clock and not the dice.
    t = time.mktime((2026, 7, 15, 14, 0, 0, 0, 0, -1))
    w.power_on(t)              # the rail comes up
    true_of = {"ph": pond3.ph, "orp": pond3.orp, "turb": pond3.turbidity}
    ks = VALUE_SPECS[slow][0]["key"]
    kf = VALUE_SPECS[fast][0]["key"]
    print(f"{'s':>4} {ks:>8} {'sant':>8} {'fel':>8} "
          f"{kf:>8} {'sant':>8} {'fel':>8}")
    for sec in [0, 2, 5, 10, 15, 20, 25, 30, 35]:
        now = t + sec
        vals = build_values(pond3, w, now, DEFAULT_FAULTS)
        s_read, s_true = _read(vals, ks), true_of[ks](now)
        f_read, f_true = _read(vals, kf), true_of[kf](now)
        print(f"{sec:4} {s_read:8.2f} {s_true:8.2f} {s_read - s_true:+8.2f} "
              f"{f_read:8.1f} {f_true:8.1f} {f_read - f_true:+8.1f}")
    print("\n  En givare som lases for tidigt efter strompaslag ger fel varde,")
    print("  och felet ser ut som ett rimligt tal. Darfor vantar pollaren.")

    # -- where the warm-up times actually come from -------------------------
    # This is the number that drives the energy budget, so who said it
    # matters as much as what it says.
    print("\nVarifran uppvarmningstiderna kommer\n")
    for unit in sorted(WARMUP_S):
        kalla = WARMUP_SOURCE[unit]
        mark = "  <-- MATS UPP" if kalla in ("gissning", "harledd") else ""
        print(f"  enhet {unit}  {WARMUP_LABEL[unit]:<14} "
              f"{WARMUP_S[unit]:>4.0f} s   {kalla}{mark}")
    print("\n  Ingen av dem ar en uppmatt uppstartstid. pH/ORP-givarens 30 s ar")
    print("  databladets svarstid for ORP. Grumlighetsgivarens 10 s ar en")
    print("  gissning -- dess datablad sager ingenting alls.")

    # -- signed registers: ORP goes negative --------------------------------
    print("\nSignerat register: ORP kan bli negativt\n")
    _, _, orp_scale, _ = REGISTERS["orp"]
    print(f"{'sant mV':>9} {'register':>9} {'signerat':>10} {'osignerat':>11}")
    for mv in [239.5, 0.0, -12.5, -150.0]:
        raw = to_register(mv, orp_scale, True, "orp")
        print(f"{mv:9.1f}   0x{raw:04X} {_decode(raw, orp_scale, True):10.1f} "
              f"{_decode(raw, orp_scale, False):11.1f}")
    print("  Sista kolumnen ar vad en lasare som glommer tecknet far: syrefritt")
    print("  vatten ser ut som ett omojligt hogt ORP i stallet for ett lagt.")

    # -- factory addresses --------------------------------------------------
    print("\nFabriksadresser\n")
    counts = {}
    for addr in FACTORY_ADDRESSES.values():
        counts[addr] = counts.get(addr, 0) + 1
    for unit, addr in sorted(FACTORY_ADDRESSES.items(), key=lambda kv: kv[1]):
        flag = "  <- KROCKAR" if counts[addr] > 1 else ""
        print(f"  adress {addr:>3}   {SENSOR_NAME[unit]:<18}"
              f"(enhet {unit}){flag}")
    krockar = [a for a, n in counts.items() if n > 1]
    if krockar:
        print(f"\n  Adress {krockar[0]} delas av tva sensorer. Andra den ena "
              f"innan den andra kopplas in.")
        print("  Ova med:  python3 fake_sensors.py --factory")

    # -- one map, two readers -----------------------------------------------
    # The simulator and the poller both build their tables from
    # sensor_profiles.py. This proves they still agree, and stops the test if
    # they do not: a silent divergence here means the poller reads the wrong
    # register and every number in the database is quietly wrong.
    print(f"\nRegisterkartan kommer fran profilen {PROFILE['id']}\n")
    sensor_profiles.verify_consistency()


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        selftest()
    else:
        main(factory="--factory" in sys.argv)
