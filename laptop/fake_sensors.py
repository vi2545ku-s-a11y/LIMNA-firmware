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

The level probe now sits on the lake bed rather than in the sensor basket,
but it hangs off the same RS485 bus as the rest. Nothing changes in the code
beyond its address moving from 4 to 5.

Fault injection: edit faults.json while this is running. It is re-read on
every request, so you can kill a sensor mid-run and watch the poller cope.

    python3 fake_sensors.py              normal, units 1-5
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
# Real Modbus sensors return integers, not floats. A reading of 8.90 mg/L is
# typically sent as 890 with an implied scale factor of 100.
#
# The numbers themselves live in sensor_profiles.py, in one place, because
# they belong to a sensor FAMILY and not to this simulator. Everything below
# is a view of that table. Change a register there and both this file and the
# poller follow; write one in by hand here and sensor_profiles.py --selftest
# fails loudly, which is the whole point of it.
PROFILE = sensor_profiles.load_profile()

#   unit : (address, scale, signed, payload key)
#   a scale of None means the scale is not fixed and has to be asked for
REGISTERS = sensor_profiles.value_registers(PROFILE)

# The same map with names on the fields, for the code that has to read more
# than the four numbers above.
VALUE_SPECS = {s["unit"]: s for s in sensor_profiles.value_specs(PROFILE)}

# Four of the five sensors also report their own water temperature. Only the
# conductivity one fills the payload -- it has the best resolution of the four
# and is signed, so it can express sub-zero water. The others are read as a
# cross-check.
#
#   unit : (address, scale, signed)
TEMP_REGISTERS = sensor_profiles.temp_registers(PROFILE)
TEMP_SOURCE_UNIT = sensor_profiles.temp_primary_unit(PROFILE)

# Four thermometers in the same water do not agree exactly. Each has its own
# calibration error, typically a few tenths of a degree, fixed for the life of
# the sensor. Without this the simulator would have all four return an
# identical number, the spread would always be zero, and the cross-check the
# poller does with it could never be tested.
#
# Two of the four also quantise to 0.1 C rather than 0.01 C, which adds a
# little more spread on its own.
#
# Keyed by payload key rather than by bus address: this is simulated sensor
# BEHAVIOUR, and it should follow the sensor if the addresses are ever moved
# or the family swapped.
TEMP_OFFSET_C = {"do": +0.12, "turb": -0.09, "ec": 0.00, "ph": +0.05,
                 "do2": +0.15}

# Two sensors of the same model in the same water do not read exactly alike
# either. A few hundredths of a mg/L of calibration error is what separates
# them while both are healthy, and do_d has to show that rather than a clean
# zero -- otherwise the field test cannot tell "no fouling yet" from "the
# comparison is not working".
CALIBRATION_OFFSET = {"do2": -0.06}

# A drifting sensor board takes its thermometer with it -- an ageing reference
# voltage or ADC shifts every channel, not just the interesting one. That is
# exactly the failure the four-way comparison is meant to catch, so "drift"
# moves the temperature too, more slowly than the main value.
TEMP_DRIFT_PER_HOUR = 0.08

# ...with one exception. On the second oxygen sensor "drift" does not stand
# for an ageing board but for BIOLOGICAL FOULING, which is what U-04 is there
# to measure. A biofilm over the optical window blocks light and the oxygen
# reading falls; it does nothing whatever to the thermometer inside the same
# housing. Letting it move temp_d too would make the fouling test fire the
# temperature alarm, and the two faults would stop being distinguishable --
# which is the entire value of having both checks.
DRIFT_IS_FOULING = {"do2"}

# The level probe does not have a fixed scale. A register of its own holds the
# number of decimal places (0 = whole metres, 3 = millimetres), and the real
# scale is 10**that. Which register that is comes from the profile; the poller
# reads it once at start-up instead of hardcoding the scale.
SCALE_REGISTER_SPEC = sensor_profiles.scale_register_spec(PROFILE)
LEVEL_DECIMALS = 3                     # this unit ships configured for mm


# Where a datasheet states the measuring range, the sensor cannot report
# outside it -- it saturates at the end of its scale instead. Without this a
# drifting pH electrode would happily report 15.37 pH, which no real device
# can do and which would make the drift fault look like a bug in the model.
#
# Only pH has a stated range in this profile, so only pH saturates.
#
#   unit : (lowest raw value, highest raw value)
REGISTER_LIMITS = sensor_profiles.raw_limits(PROFILE)


def to_register(value, scale, signed, unit=None):
    """Encode a float the way a real sensor would, as one 16-bit register.

    Signed values are sent as two's complement, which is why reading them
    back as unsigned turns -2.51 C into 65285.
    """
    raw = int(round(value * scale))
    if unit in REGISTER_LIMITS:
        low, high = REGISTER_LIMITS[unit]
        raw = max(low, min(high, raw))
    if signed:
        raw = max(-32768, min(32767, raw))
        return raw & 0xFFFF
    return max(0, min(65535, raw))


# ---------------------------------------------------------------------------
# FACTORY ADDRESSES  -- and the collision waiting on the bench
# ---------------------------------------------------------------------------
# Every sensor ships with a factory address that has to be changed before the
# buoy can work. Which register that is written to differs between the five,
# and none of it has been tried on hardware yet -- see the profile.
#
# CONDUCTIVITY AND pH BOTH SHIP ON ADDRESS 1.
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
# real lake: oxygen swings over the day, pH swings with it, and rain moves
# several parameters at once in a correlated way.

class Lake:
    """A shallow eutrophic lake in Skane in July."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.t0 = time.time()
        # Slow random walk so values drift instead of jumping
        self._walk = {"do": 0.0, "turb": 0.0, "ec": 0.0,
                      "lvl": 0.0, "temp": 0.0, "ph": 0.0}
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

    # -- quantities ---------------------------------------------------------
    def temperature(self, now):
        """Water temperature. Small diurnal swing, lags the air by ~3 hours."""
        h = self._hour_of_day(now)
        diurnal = 0.8 * math.sin(2 * math.pi * (h - 9.0) / 24.0)
        return 18.4 + diurnal + self._drift("temp", 0.02, 0.5)

    def dissolved_oxygen(self, now, declining=False):
        """
        Diurnal cycle: plants produce oxygen in daylight and consume it at
        night, so the minimum falls just before dawn and the peak in the late
        afternoon. This is the single most important pattern for the product,
        because a bloom shows up as the night minimum sinking week by week.
        """
        h = self._hour_of_day(now)
        base = 9.2
        diurnal = 1.8 * math.sin(2 * math.pi * (h - 10.0) / 24.0)
        value = base + diurnal + self._drift("do", 0.02, 0.4)
        if declining:
            # Simulated bloom: oxygen falls steadily. Used for alarm testing.
            hours_running = (now - self.t0) / 3600.0
            value -= 1.2 * hours_running
        return max(0.0, value)

    def ph(self, now, declining=False):
        """
        pH tracks oxygen through the day, and that is not a coincidence: both
        are driven by photosynthesis. In daylight the plants strip carbon
        dioxide out of the water, which removes carbonic acid and pushes pH
        up, and they release oxygen while doing it. After dark respiration
        reverses both. The two curves rising and falling TOGETHER is a real
        property of a productive lake, and a pH curve that runs opposite to
        the oxygen curve means the model -- or the sensor -- is wrong.

        Hence the same phase term as dissolved_oxygen() above. If you change
        one, change the other.
        """
        h = self._hour_of_day(now)
        base = 8.2                                    # eutrophic lake in July
        diurnal = 0.4 * math.sin(2 * math.pi * (h - 10.0) / 24.0)
        value = base + diurnal + self._drift("ph", 0.004, 0.12)
        # Rainwater is mildly acidic and dilutes the lake's buffering capacity
        value -= 0.5 * self._rain_factor(now)
        if declining:
            # Same cause as the oxygen decline: decomposition consumes oxygen
            # and produces carbon dioxide, which acidifies the water.
            hours_running = (now - self.t0) / 3600.0
            value -= 0.25 * hours_running
        return max(0.0, min(14.0, value))

    def turbidity(self, now):
        """Clear baseline, big spike after rain as the catchment washes in."""
        base = 4.1 + self._drift("turb", 0.05, 1.0)
        return max(0.0, base + 34.0 * self._rain_factor(now))

    def conductivity(self, now):
        """Stable, dips slightly when diluted by rain."""
        base = 312.0 + self._drift("ec", 0.3, 8.0)
        return max(0.0, base - 45.0 * self._rain_factor(now))

    def level(self, now):
        """Slow baseline variation, rises during rain."""
        base = 2.340 + self._drift("lvl", 0.001, 0.05)
        return base + 0.18 * self._rain_factor(now)


# ---------------------------------------------------------------------------
# FAULT INJECTION
# ---------------------------------------------------------------------------

# One line per sensor in the profile, named after its address and its payload
# key, so a sixth sensor or a swapped family brings its own line along.
FAULT_KEYS = {s["unit"]: f"sensor_{s['unit']}_{s['key']}"
              for s in sensor_profiles.value_specs(PROFILE)}


def default_faults():
    """The faults.json written on first run, built from the profile."""
    faults = {"_comment": "Edit while running. Re-read on every request."}
    for unit in sorted(FAULT_KEYS):
        faults[FAULT_KEYS[unit]] = "ok"
    faults["_states"] = "ok | offline | stuck | noisy | drift"
    faults["rain_event"] = False
    faults["oxygen_decline"] = False
    # The adaptive warm-up hypothesis, off by default. See the note next to
    # cold_returns_zero() below -- this makes the simulator PLAY the
    # hypothesis, it does not make the hypothesis true.
    faults["cold_returns_zero"] = False
    return faults


DEFAULT_FAULTS = default_faults()

# Value a "stuck" sensor freezes at. Deliberately unlike any real reading so
# it is recognisable in a log. Keyed by payload key: this is behaviour of the
# simulated quantity, not of the bus address it happens to sit on.
STUCK_VALUES = {"do": 8.88, "turb": 4.44, "ec": 300.0,
                "ph": 7.77, "lvl": 2.222, "do2": 8.88}

# "drift" moves the reading by this much per hour. The sensor keeps answering
# and the value keeps looking plausible -- it is simply wrong, and gets more
# wrong the longer it runs.
#
# This is the pH electrode's real failure mode and the actual maintenance
# problem with that sensor. A glass electrode ages: the reference junction
# fouls and the slope degrades, so the reading walks away from the truth over
# weeks. It never stops answering. It starts lying, slowly. Nothing in the
# alarm rules catches this yet -- catching it needs a calibration schedule or
# a second reference, not a threshold.
DRIFT_PER_HOUR = {"do": 0.4, "turb": 0.5, "ec": 6.0,
                  "ph": 0.15, "lvl": 0.02,
                  # NEGATIVE for the second oxygen sensor, and that is the
                  # point of U-04: biofilm on the optical window blocks light
                  # and the sensor reads LOW. Set "drift" on sensor_6_do2
                  # alone and do_d walks away from its calibration gap while
                  # both sensors keep answering normally -- which is exactly
                  # what fouling looks like from the shore.
                  #
                  # ACCELERATED FOR TESTING, NOT A PREDICTION. Real fouling
                  # builds over WEEKS -- the datasheet's cleaning interval is
                  # 30 days. At -0.35 mg/L per hour a test run reaches a
                  # visible gap in an afternoon instead of in a month. Do not
                  # read this number as an expected rate, and do not set an
                  # alarm threshold from it. The rate the field test is meant
                  # to MEASURE is the one that matters, and nobody knows it
                  # yet -- that is what U-04 is for.
                  "do2": -0.35}


def read_faults():
    try:
        with open(FAULTS_FILE) as fh:
            return json.load(fh)
    except Exception:
        return DEFAULT_FAULTS


def ensure_faults_file():
    """Create faults.json, or add rows for sensors it does not know about.

    Fitting the optional second oxygen sensor should not mean hand-editing
    this file to be able to break it. Existing rows are never touched -- a
    fault you set stays set.
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
# Where each figure comes from is part of the profile, not of this file, and
# only TWO of the five are stated by a manufacturer. The distinction matters
# because the two that are not stated are the two expensive ones: oxygen and
# turbidity are guesses, conductivity and pH come from the datasheets, and the
# level probe's is DERIVED from a 5 ms response time -- how fast it answers
# once it is running, not how long it takes to get there.
#
# The self test prints this table, so the guesses stay visible to whoever runs
# the code rather than only to whoever reads this comment.
WARMUP_S = sensor_profiles.warmup_times(PROFILE)
WARMUP_SOURCE = sensor_profiles.warmup_sources(PROFILE)
WARMUP_LABEL = sensor_profiles.warmup_labels(PROFILE)

# THE AWKWARD FACT: the two sensors that draw the most current are the two
# that are ready fastest. Holding all five powered for 90 seconds because the
# oxygen sensor needs it costs roughly 5 Wh per day out of a 12.8 Wh budget,
# and that is the difference between meeting the autonomy requirement and
# missing it.
#
# The fix is two switched rails rather than one, so the fast group can be read
# and cut while the oxygen sensor is still settling (requirements E-11, E-12).
# That is a hardware change and is NOT built yet. The grouping is a per-sensor
# attribute in the profile, so splitting it means giving each rail its own
# MOSFET and calling power_on() twice -- not restructuring this file.
#
#   slow = oxygen and turbidity, long warm-up, modest current
#   fast = conductivity, pH and level, ready in seconds, high current
POWER_RAIL = sensor_profiles.power_rails(PROFILE)


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

    def power_on(self, now, rail=None):
        """Switch on one rail, or all of them when rail is None.

        Today the poller always brings everything up at once, so rail stays
        None. The argument exists so that a two-rail buoy can call
        power_on(now, "fast") and power_on(now, "slow") at different moments
        without this class changing.
        """
        for unit in WARMUP_S:
            if rail is None or POWER_RAIL[unit] == rail:
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

def apply_rain_state(lake, faults, now):
    """Start or clear a rain event, following faults.json."""
    if faults.get("rain_event"):
        if lake.rain_started_at is None:
            lake.rain_started_at = now
            print("  [handelse]  regn startat")
    else:
        lake.rain_started_at = None


def lake_value(lake, key, now, faults):
    """The true value of one measured quantity, before any fault or warm-up.

    Dispatches on the payload key, not on the bus address, so moving a sensor
    to another address -- or swapping the whole family -- changes nothing here.
    """
    declining = bool(faults.get("oxygen_decline", False))
    if key in ("do", "do2"):
        # Both oxygen sensors sit in the same water and see the same lake.
        # What separates them is their calibration error, and -- once one of
        # them fouls -- the drift fault on that unit alone.
        return (lake.dissolved_oxygen(now, declining=declining)
                + CALIBRATION_OFFSET.get(key, 0.0))
    if key == "turb":
        return lake.turbidity(now)
    if key == "ec":
        return lake.conductivity(now)
    if key == "ph":
        return lake.ph(now, declining=declining)
    if key == "lvl":
        return lake.level(now)
    raise SystemExit(
        f"Profilen innehaller givaren '{key}', men vattenmodellen i "
        f"fake_sensors.py vet inte hur den storheten beter sig.\n"
        f"Lagg till den i lake_value() och i klassen Lake.")


def build_unit(lake, warmup, unit, now, faults):
    """Return {register address: raw_int} for ONE sensor, or None if offline."""
    spec = VALUE_SPECS[unit]
    key = spec["key"]
    state = faults.get(FAULT_KEYS[unit], "ok")
    if state == "offline":
        return None                        # unit simply absent

    value = lake_value(lake, key, now, faults)

    if state == "stuck":
        value = STUCK_VALUES[key]
    elif state == "noisy":
        value *= (1.0 + lake.rng.gauss(0, 0.25))
    elif state == "drift":
        value += DRIFT_PER_HOUR[key] * ((now - lake.t0) / 3600.0)

    # THE ADAPTIVE WARM-UP HYPOTHESIS, playable on demand.
    #
    # Default behaviour is what we have measured curves for: an unsettled
    # sensor answers with a WRONG NUMBER, drifting in towards the truth. Set
    # cold_returns_zero in faults.json and it instead answers 0 until it is
    # settled, the way Seeed's CO2 sensor is documented to do.
    #
    # That switch exists so the poller's adaptive_warmup path can be
    # exercised against both worlds. Which of the two our five sensors
    # actually live in is UNKNOWN, and running this simulator proves nothing
    # about it -- it only does what this flag says.
    if faults.get("cold_returns_zero") and warmup.settle_fraction(unit, now) < 1.0:
        regs = {spec["addr"]: 0}
        if spec["scale_register"] is not None:
            regs[spec["scale_register"]] = LEVEL_DECIMALS
        if unit in TEMP_REGISTERS:
            regs[TEMP_REGISTERS[unit][0]] = 0
        return regs

    # Apply warm-up: an unsettled sensor reads low and wanders.
    #
    # The noise is drawn from the lake's own generator, not the global random
    # module. Seeding a Lake then reading it through an unseeded global made
    # the self test print different warm-up numbers on every run, which is no
    # use at all for checking that a change to this code left the behaviour
    # intact -- and that check is exactly what this simulation exists for.
    # Lake() without a seed is still unpredictable, so live runs are unchanged.
    f = warmup.settle_fraction(unit, now)
    if f < 1.0:
        value = value * (0.55 + 0.45 * f) + lake.rng.gauss(0, (1 - f) * 0.6)

    addr, scale, signed = spec["addr"], spec["scale"], spec["signed"]
    if spec["scale_register"] is not None:
        # This sensor's scale is not fixed: it reports the number of decimals
        # in a register of its own, and a reader that ignores that register
        # gets a number a thousand times too big.
        scale = 10 ** LEVEL_DECIMALS
        regs = {addr: to_register(value, scale, signed, unit),
                spec["scale_register"]: LEVEL_DECIMALS}
    else:
        regs = {addr: to_register(value, scale, signed, unit)}

    if unit in TEMP_REGISTERS:
        t_addr, t_scale, t_signed = TEMP_REGISTERS[unit]
        t_value = lake.temperature(now) + TEMP_OFFSET_C.get(key, 0.0)
        if state == "drift" and key not in DRIFT_IS_FOULING:
            t_value += TEMP_DRIFT_PER_HOUR * ((now - lake.t0) / 3600.0)
        elif state == "stuck":
            t_value = 11.11
        regs[t_addr] = to_register(t_value, t_scale, t_signed)
    return regs


def build_values(lake, warmup, now, faults):
    """Return {unit: {register: raw_int}} for all five sensors at once.

    Only the self test uses this. The server builds one unit at a time, at
    the moment that unit is actually read.
    """
    out = {}
    for unit in REGISTERS:
        regs = build_unit(lake, warmup, unit, now, faults)
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

    def __init__(self, unit, lake, warmup, bus_address=None, **kwargs):
        super().__init__(**kwargs)
        self.unit = unit
        self.lake = lake
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
        # The address register sits far above the value registers -- 0x2000
        # for three of ours -- so it is outside the block allocated for
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
        apply_rain_state(self.lake, faults, now)
        regs = build_unit(self.lake, self.warmup, self.unit, now, faults)
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
        """
        if address != self.address_register():
            return super().setValues(fc_as_hex, address, values)

        ny = int(values[0])
        if not 1 <= ny <= 247:
            # Modbus RTU addresses stop at 247. A real sensor would reject
            # this; so does this one, by ignoring it.
            print(f"  [ADRESS] {REGISTERS[self.unit][3]} vagrar adress {ny} "
                  f"-- giltiga adresser ar 1-247.")
            return

        gammal = self.bus_address
        if ny == gammal:
            return
        if self.server is not None:
            self.server[ny] = self
            del self.server[gammal]
        self.bus_address = ny
        print(f"  [ADRESS] {REGISTERS[self.unit][3]} flyttad fran adress "
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
    """

    def __init__(self, units, **kwargs):
        super().__init__(**kwargs)
        self.units = units
        self._warned = False

    def validate(self, fc_as_hex, address, count=1):
        if not self._warned:
            names = " och ".join(REGISTERS[u][3] for u in self.units)
            print(f"\n  [KROCK] Tva sensorer ({names}) svarar bada pa adress 1.")
            print("          Pa den riktiga bussen sander de samtidigt och "
                  "svaret blir olasligt.")
            print("          Mastern ser bara en timeout och rapporterar "
                  "'svarar inte'.")
            print("          Andra adress pa den ena INNAN den andra "
                  "kopplas in.\n")
            self._warned = True
        return False


def build_server_context(lake, warmup, factory=False):
    """Map units onto bus addresses, normally 1-5, optionally the factory set."""
    from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext

    def block():
        # 0x0101 is the highest address we answer on, so 0x0110 registers is
        # comfortably enough for every sensor.
        return ModbusSequentialDataBlock(0, [0] * 0x0110)

    slaves = {}
    if not factory:
        for unit in REGISTERS:
            slaves[unit] = SensorContext(unit, lake, warmup,
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
                slaves[addr] = SensorContext(units[0], lake, warmup,
                                             bus_address=addr, hr=block())
            else:
                slaves[addr] = CollisionContext(units, hr=block())

    context = ModbusServerContext(slaves=slaves, single=False)
    # Each sensor needs to reach the collection it sits in, so that writing
    # its address register can move it from one entry to another -- which is
    # what a real sensor does when you re-address it.
    for slave in slaves.values():
        if isinstance(slave, SensorContext):
            slave.server = context
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

    lake = Lake()
    warmup = WarmupTracker()
    context = build_server_context(lake, warmup, factory=factory)

    def rail_power(connected):
        """The poller opening the bus is the sensor rail being switched on.

        With split_rail on, the poller opens one connection per power
        channel, so each connection is that channel coming up. Powering every
        unit here is still correct for that: the channels are switched on one
        after the other, and the next connection restarts the clock for the
        sensors that had not been read yet -- which is exactly what a second
        MOSFET closing at that moment does.
        """
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
                  f"{REGISTERS[unit][3]}  andras via "
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


def selftest():
    print("Vattenmodell, ett dygn i steg om tva timmar\n")
    lake = Lake(seed=1)
    base = time.time()

    print(f"{'kl':>5} {'temp':>7} {'syre':>7} {'pH':>7} {'turb':>7} "
          f"{'kond':>7} {'niva':>7}")
    for step in range(0, 24, 2):
        now = base + step * 3600
        print(f"{time.strftime('%H:%M', time.localtime(now)):>5} "
              f"{lake.temperature(now):7.2f} "
              f"{lake.dissolved_oxygen(now):7.2f} "
              f"{lake.ph(now):7.2f} "
              f"{lake.turbidity(now):7.2f} "
              f"{lake.conductivity(now):7.1f} "
              f"{lake.level(now):7.3f}")

    # -- pH must track oxygen, not oppose it --------------------------------
    print("\npH och syre ska rora sig at SAMMA hall over dygnet\n")
    lake_c = Lake(seed=7)
    prev_do = prev_ph = None
    agree = total = 0
    for step in range(0, 24):
        now = base + step * 3600
        d, p = lake_c.dissolved_oxygen(now), lake_c.ph(now)
        if prev_do is not None:
            total += 1
            if (d - prev_do) * (p - prev_ph) > 0:
                agree += 1
        prev_do, prev_ph = d, p
    print(f"  {agree} av {total} timsteg ror sig at samma hall")
    print(f"  {'OK' if agree == total else 'FEL -- modellen ar felaktig'}")

    print("\nRegnhandelse, timme for timme fran start\n")
    lake2 = Lake(seed=2)
    lake2.rain_started_at = base
    print(f"{'h':>4} {'turb':>7} {'kond':>7} {'niva':>7} {'pH':>7}")
    for h in [0, 0.25, 0.5, 1, 2, 3, 4, 6, 8]:
        now = base + h * 3600
        print(f"{h:4} {lake2.turbidity(now):7.2f} "
              f"{lake2.conductivity(now):7.1f} {lake2.level(now):7.3f} "
              f"{lake2.ph(now):7.2f}")

    # -- warm-up, oxygen against pH -----------------------------------------
    print("\nUppvarmning: syret tar 40 s, pH ar klart pa ett par sekunder\n")
    lake3 = Lake(seed=3)
    w = WarmupTracker()
    # A fixed instant rather than time.time(), so this table is byte for byte
    # the same on every run. Together with the seeded lake that turns it into
    # a regression check: change anything in the warm-up code and whatever
    # moves here is the change, not the clock and not the dice.
    t = time.mktime((2026, 7, 15, 14, 0, 0, 0, 0, -1))
    w.power_on(t)              # the rail comes up
    print(f"{'s':>4} {'syre':>8} {'sant':>8} {'fel':>8} "
          f"{'pH':>8} {'sant':>8} {'fel':>8}")
    for s in [0, 2, 5, 10, 20, 30, 40, 50]:
        now = t + s
        vals = build_values(lake3, w, now, DEFAULT_FAULTS)
        do_true = lake3.dissolved_oxygen(now)
        ph_true = lake3.ph(now)
        do_read = _decode(vals[1][REGISTERS[1][0]], REGISTERS[1][1], False)
        ph_read = _decode(vals[4][REGISTERS[4][0]], REGISTERS[4][1], False)
        print(f"{s:4} {do_read:8.2f} {do_true:8.2f} {do_read - do_true:+8.2f} "
              f"{ph_read:8.2f} {ph_true:8.2f} {ph_read - ph_true:+8.2f}")

    # -- where the warm-up times actually come from -------------------------
    # This is the number that drives 72 per cent of the energy budget, so who
    # said it matters as much as what it says.
    print("\nVarifran uppvarmningstiderna kommer\n")
    for unit in sorted(WARMUP_S):
        kalla = WARMUP_SOURCE[unit]
        mark = "  <-- MATS UPP" if kalla == "gissning" else ""
        print(f"  enhet {unit}  {WARMUP_LABEL[unit]:<14} "
              f"{WARMUP_S[unit]:>4.0f} s   {kalla}{mark}")
    gissade = [u for u in WARMUP_S if WARMUP_SOURCE[u] == "gissning"]
    ur_datablad = [u for u in WARMUP_S if WARMUP_SOURCE[u] == "datablad"]
    print(f"\n  {len(ur_datablad)} av {len(WARMUP_S)} star i datablad. "
          f"Nivan ar harledd ur en svarstid pa 5 ms,")
    print("  vilket inte ar samma sak som uppstartstid.")
    print(f"  Energibudgetens 90 s vilar pa enhet {gissade[0]}, "
          f"som ingen tillverkare angett.")

    # -- signed registers ---------------------------------------------------
    print("\nSignerade register: en minusgrad ska lasas som minusgrad\n")
    _, t_scale, t_signed = TEMP_REGISTERS[TEMP_SOURCE_UNIT]
    print(f"{'sant':>8} {'raatt':>8} {'signerat':>10} {'osignerat':>11}")
    for c in [18.40, 0.00, -0.50, -2.51, -10.00]:
        raw = to_register(c, t_scale, t_signed)
        ok = _decode(raw, t_scale, True)
        bad = _decode(raw, t_scale, False)
        print(f"{c:8.2f} {raw:8d} {ok:10.2f} {bad:11.2f}")
    print("  Sista kolumnen ar vad den gamla koden hade gjort.")

    # -- level scale comes from the sensor, not from us ---------------------
    print(f"\n{SCALE_REGISTER_SPEC['label'].capitalize()}givarens skala lases "
          f"ur register 0x{SCALE_REGISTER_SPEC['scale_register']:04X}\n")
    for dec in [0, 1, 2, 3]:
        scale = 10 ** dec
        raw = to_register(2.340, scale, True)
        print(f"  decimaler={dec}  ->  skala {scale:>5}  "
              f"ratt {raw / scale:6.3f} m   "
              f"om man antar 1000: {raw / 1000:7.4f} m")

    # -- factory addresses --------------------------------------------------
    print("\nFabriksadresser\n")
    counts = {}
    for addr in FACTORY_ADDRESSES.values():
        counts[addr] = counts.get(addr, 0) + 1
    for unit, addr in sorted(FACTORY_ADDRESSES.items(), key=lambda kv: kv[1]):
        flag = "  <- KROCKAR" if counts[addr] > 1 else ""
        print(f"  adress {addr:>3}   {REGISTERS[unit][3]:<5}"
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
