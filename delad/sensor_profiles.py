#!/usr/bin/env python3
"""
LIMNA - sensor profiles.

THE REGISTER MAP LIVES HERE, AND NOWHERE ELSE.

Both fake_sensors.py and poller.py read this file. Neither keeps a copy of
its own, and

    python3 sensor_profiles.py --selftest

proves that the two still see the same map. Until 2026-08-09 the map was
written out twice -- REGISTERS in the simulator and SENSORS in the poller --
and changing one left the other reading the wrong register with nothing to
complain about it.

A PROFILE DESCRIBES ONE SENSOR FAMILY

The family below is the irrigation pond buoy as decided 2026-11-23: two
Seeed (SenseCAP) RS485 sensors, a pH/ORP/temperature probe and a turbidity
probe. Both register maps are taken from the manufacturer's manuals, see
sensor_databla.md. Neither has been tried against real hardware yet.

    portable        the operating addresses 1-2. We set those ourselves at
                    assembly time and they do not depend on the make.
    product         factory addresses, register addresses, scale factors,
    specific        signed or unsigned, measuring ranges, warm-up times,
                    the address-change register and the cable colours.

ONE SENSOR, SEVERAL VALUES

A sensor row is one physical device on one bus address. It can report more
than one quantity: the pH probe gives pH AND ORP from the same address. Each
quantity is one entry in the row's "values" list and becomes one field in
the payload. Everything about the device itself -- address, warm-up, power,
cable -- is said once, on the row.

HOW TO ADD A NEW FAMILY

    1. Copy LIMNA_DAMM_2026 below, give it a new id and fill it in FROM THE
       DATASHEETS. Name the datasheet in "kalla".
    2. Add it to PROFILES.
    3. Point config.json at it:   "sensor_profile": "din-nya-profil"
    4. Run  python3 sensor_profiles.py --selftest  and read the printed map.

That is the whole change. Nothing in fake_sensors.py or poller.py should need
touching to swap families -- except the water model in fake_sensors.py, which
has to know how a new quantity behaves.

WHAT TO VERIFY AGAINST THE DATASHEET BEFORE USING A NEW PROFILE

    addr        the register address of the value itself.
    scale       the implied divisor. Differs between values on the SAME
                sensor -- ours reads pH in hundredths and ORP in tenths.
    signed      a Modbus register is 16 bits and it is the READER that has to
                know whether they are two's complement. Read a signed one as
                unsigned and -12.5 mV comes back as 6541.1 mV, which looks
                like a number in a log.
    range       the measuring range, used to saturate the simulator and to
                flag impossible readings. Leave it None when the datasheet
                does not state one; never invent it.
    factory_unit    the address the sensor ships on. Ours both ship on 1,
                which is a wiring trap, not a code problem.
    address_register    where the new address is written.
    warmup_s / warmup_source    how long after power-up the reading can be
                trusted, and WHO SAYS SO. This number drives the energy
                budget, so a guess goes in as "gissning" and a figure worked
                out from something else as "harledd" -- never as a bare
                number.
    rail        which switched power channel the sensor hangs off. The fast
                group can be read and cut while the slow group settles
                (requirements E-11 and E-12).
    effekt_w    power draw as (lowest, highest) watts at the buoy's 12 V, or
                None when the manufacturer does not publish one. Used only to
                put a figure on what a split rail would save -- never to
                decide anything on its own.
    skipped     registers the sensor offers that we deliberately do not read.
                Written down so the next reader knows they were considered
                and rejected, not missed.
    stuck_cycles    how many measurements in a row this value may be
                bit-identical before it is suspected of being frozen. It
                follows from the RESOLUTION of the register: a value
                quantised to whole units can honestly repeat for hours in
                still water, one quantised to hundredths cannot.
"""

import json
import os
import sys

def _here():
    """The folder this file sits in, or "" on the buoy.

    MicroPython has no os.path. On the ESP32 this file is copied to the root
    of the board's filesystem, next to config.json, so "" -- the current
    folder -- is the right answer there. Solved here rather than in a copy
    of this file, because a copy is exactly what this file exists to prevent.
    """
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except (AttributeError, NameError):
        return ""


HERE = _here()

# WHICH config.json THIS FILE READS
#
# Only two things are asked of it: which profile is fitted, and which optional
# sensors. But this file lives in delad/ and is shared with the firmware,
# while config.json belongs to the program that runs -- laptop/config.json for
# the rehearsal, and something else entirely on the buoy. Guessing "next to
# myself" would look for delad/config.json, which nothing ever writes.
#
# So the caller says. poller.py and fake_sensors.py both call use_config()
# before loading a profile. The default below only matters when this file is
# run on its own, as its own self test.
CONFIG_FILE = (HERE + "/" if HERE else "") + "config.json"


def use_config(path):
    """Point the profile loader at the config the running program uses.

    Called by whoever owns the config file, so that the profile and the
    poller can never end up reading two different ones.
    """
    global CONFIG_FILE
    CONFIG_FILE = path

DEFAULT_PROFILE = "limna-damm-2026"


# ---------------------------------------------------------------------------
# PROFILE: LIMNA in an irrigation pond, the sensors chosen 2026-11-23
# ---------------------------------------------------------------------------
# The buoy floats in a farm's irrigation pond, runs for the irrigation season
# only (roughly April to October) and is lifted out before the ice.
#
# TWO SENSORS ON ONE BUS, BOTH FROM THE SAME MAKER.
#
#   1  pH, ORP, temp   Seeed SenseCAP S-pH&ORP-01A, SKU 100050803.
#                      "RS485 pH, ORP and Temperature Sensor User Manual" V1.0.
#   2  turbidity, temp Seeed SenseCAP S-DTS210-01, SKU 101990793.
#                      "Industrial Turbidity Sensor User Manual" V1.0.0,
#                      2025-01-21.
#
# Both maps are from the manuals, not from a real sensor. The one thing the
# manuals disagree with THEMSELVES about is the turbidity probe's factory
# address -- see its row.
#
# WHY THE INTEGER REGISTERS AND NOT THE FLOATS
#
# The pH probe also offers every value as an IEEE 754 float (0x1000 up). Its
# word order is a setting, FLOATBYTEORDER at 0x0023, and the factory setting
# is 3 = CDAB -- low word first, the opposite of what most readers assume. The
# integer registers carry the same resolution as the datasheet promises
# (0.01 pH, 0.1 mV) with no word order to get wrong, so those are what we
# read. The turbidity probe has no floats at all.

LIMNA_DAMM_2026 = {
    "id": "limna-damm-2026",
    "namn": "LIMNA bevattningsdamm 2026 (Seeed pH/ORP + Seeed grumlighet)",
    "verifierad": "2026-11-23 mot tillverkarens manualer, ej mot hardvara",
    "kalla": "sensor_databla.md",

    # 9600 8N1 for the whole bus. Both sensors ship on it, so -- unlike the
    # sensors this profile replaced -- no baud rate has to be changed on the
    # bench. Only the addresses have to be.
    "serie": {"baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1},

    # Both sensors have a thermometer, so temp_d -- the largest gap between
    # them -- is in every payload. The turbidity probe's is the one published
    # as temp: it is a Pt1000 stated at +-0.3 C, against the pH probe's
    # +-0.5 C. Two healthy thermometers can therefore honestly differ by up
    # to 0.8 C, which is what TEMP_D_TOLERANCE_C below says.
    "temp_primar": "turb",

    "adressregister_status": "ur tillverkarens manualer, ej provat mot hardvara",

    "sensors": [
        {
            "id": "ph",
            "label": "pH/ORP-givare",
            "kort": "ph/orp",
            "modell": "Seeed S-pH&ORP-01A",
            "unit": 1,
            # Factory address 1, like the turbidity probe: they COLLIDE.
            # Move one of them before the other is connected.
            "factory_unit": 1,
            # Manual 5.2: ADDRESS 0x0200, 1-247, function 0x06. Manual 5.3:
            # the new address takes effect only after the sensor has been
            # restarted -- power-cycle it, or write 0xFFFF to 0x0051.
            "address_register": 0x0200,
            "adressbyte_not": ("Ny adress galler forst efter omstart: bryt "
                               "strommen till givaren och sla pa igen."),
            "values": [
                {
                    # 0x0001 PH, INT16, 0-1400 -> 0.00-14.00 pH, temperature
                    # compensated to 25 C (TEMPCOMPENSATEEN 0x0022 = 0, the
                    # factory setting).
                    "key": "ph", "label": "pH",
                    "addr": 0x0001, "scale": 100, "signed": True,
                    "range": (0.0, 14.0), "stuck_cycles": 24,
                },
                {
                    # 0x0002 ORP, INT16, -20000-20000 -> -2000.0-2000.0 mV,
                    # temperature compensated. Negative in anoxic water, so
                    # signed matters here for real.
                    "key": "orp", "label": "ORP",
                    "addr": 0x0002, "scale": 10, "signed": True,
                    "range": (-2000.0, 2000.0), "stuck_cycles": 24,
                },
            ],
            # 0x0000 TEMPERATURE_CALIBED, INT16, -4000-8000 -> -40.00-80.00 C,
            # with the user offset TOFFSET (0x0021) applied. Stated resolution
            # is 0.1 C even though the register counts hundredths.
            "temp": {"addr": 0x0000, "scale": 100, "signed": True},
            # The datasheet gives RESPONSE times, not a start-up time: pH
            # under 10 s, ORP under 30 s. A reading taken before the
            # electrodes have settled is exactly what a response time
            # describes, so the slower of the two is the honest first figure.
            # Measure it.
            "warmup_s": 30.0,
            "warmup_source": "harledd",
            "rail": "slow",
            # "Quiescent <8 mA at 12 V DC; measuring <10 mA at 12 V DC".
            # Upper bounds, so this is the most it draws, not what it draws.
            "effekt_w": (0.12, 0.12),
            "effekt_kalla": "datablad, under 10 mA vid 12 V (ovre grans)",
            "matning": "4,5-28 V DC",
            "skipped": [
                (0x0003, "pH-elektrod mV", "ravarde, pH i 0x0001 racker"),
                (0x0004, "pH okompenserat", "vi vill ha 25 C-kompenserat"),
                (0x0007, "ORP okompenserat", "vi vill ha kompenserat"),
                (0x0009, "temp okalibrerad", "0x0000 har offset inlagd"),
                (0x1000, "flyttal 0x1000-", "ordflyttat CDAB, heltalen racker"),
                (0x0030, "kalibrering", "skrivs bara vid kalibrering"),
            ],
            "kabel": ("rod = +, svart = GND, GUL = A+, VIT = B-. 2 m kabel "
                      "med hylsor."),
        },
        {
            "id": "turb",
            "label": "grumlighetsgivare",
            "kort": "grumlighet",
            "modell": "Seeed S-DTS210-01",
            "unit": 2,
            # THE MANUAL CONTRADICTS ITSELF. Its register table (5.2) says
            # the address register's default is 50. Every command example
            # (5.3) talks to address 01 and calls 01 the "default address".
            # 1 is used here because it is what the examples were evidently
            # run against; set_address.py --scan also asks address 50, and
            # whichever answers on the first real sensor settles it.
            "factory_unit": 1,
            "factory_candidates": [1, 50],
            # Manual 5.2: Sensor Address 0x2000, 1-255, function 0x06. Saved
            # through power-off; the manual does not say a restart is needed.
            "address_register": 0x2000,
            "adressbyte_not": ("Manualen anger fabriksadress bade 1 och 50. "
                               "Kor --scan; svarar den pa 50, anvand "
                               "--from 50."),
            "values": [
                {
                    # 0x0101 Turbidity Value, unsigned, NTU x 10. The manual's
                    # own example: 0x03D9 = 985 -> 98.5 NTU.
                    "key": "turb", "label": "grumlighet",
                    "addr": 0x0101, "scale": 10, "signed": False,
                    "range": (0.0, 1000.0), "stuck_cycles": 24,
                },
            ],
            # 0x0100 Temperature Value, unsigned, C x 10. Unsigned, so it
            # cannot report below 0 C -- the operating range is 0-50 C, and a
            # pond with ice on it is one the buoy has already been lifted from.
            "temp": {"addr": 0x0100, "scale": 10, "signed": False},
            # NOTHING IN THE DATASHEET OR THE MANUAL. Calibration asks for
            # 3-5 minutes in the standard solution, but that is the sample
            # settling around a sensor that is already on, not a start-up
            # time. 10 s is a guess for an infrared LED and a photodiode, and
            # it is marked as one. Measure it.
            "warmup_s": 10.0,
            "warmup_source": "gissning",
            "rail": "fast",
            "effekt_w": (0.30, 0.30),
            "effekt_kalla": "datablad, 0,3 W vid 12 V",
            # The narrower of the two: below 12 V this sensor is outside its
            # specification. A 4S LiFePO4 battery is allowed to fall to
            # 10.8 V, so the rail has to be regulated, not the battery
            # straight through.
            "matning": "12-24 V DC (typiskt 12 V)",
            "skipped": [
                (0x0000, "varde+decimaler", "samma tal, 0x0100-0x0101 racker"),
                (0x1000, "kalibrering", "skrivs bara vid kalibrering"),
                (0x2003, "baud", "9600 fran fabrik, ingen andring"),
                (0x2020, "fabriksaterstall", "RADERAR KALIBRERINGEN, skriv ej"),
            ],
            "kabel": ("rod = +, svart = GND, BLA = A, VIT = B. M16-kontakt, "
                      "10 m kabel. Ingen gul ledare: pa pH-givaren ar GUL "
                      "A. Koppla efter funktion, inte farg."),
        },
    ],
}

# How far apart two healthy thermometers in the same water may be, from the
# two datasheets' accuracy figures (+-0.5 C and +-0.3 C). A larger temp_d is
# worth a look; it is not by itself a fault.
TEMP_D_TOLERANCE_C = 0.8


PROFILES = {
    LIMNA_DAMM_2026["id"]: LIMNA_DAMM_2026,
}


# ---------------------------------------------------------------------------
# LOADING
# ---------------------------------------------------------------------------

def profile_name_from_config():
    """Which profile config.json asks for, or the default if it says nothing."""
    try:
        with open(CONFIG_FILE) as fh:
            return json.load(fh).get("sensor_profile", DEFAULT_PROFILE)
    except Exception:
        # config.json is written by the poller on first run, so it is normal
        # for it not to exist yet.
        return DEFAULT_PROFILE


def optional_from_config():
    """Which optional sensors config.json says are actually fitted."""
    try:
        with open(CONFIG_FILE) as fh:
            return list(json.load(fh).get("optional_sensors", []))
    except Exception:
        return []


def load_profile(name=None, optional=None):
    """Return the named profile, or the one config.json points at.

    Sensors marked "valfri" are left out unless their id is named in
    optional_sensors. Use it for a sensor that is fitted for a trial and
    taken off again afterwards: the buoy must run identically either way.
    """
    if name is None:
        name = profile_name_from_config()
    if name not in PROFILES:
        raise SystemExit(
            f"Okand sensorprofil '{name}'.\n"
            f"Valjbara profiler: {', '.join(sorted(PROFILES))}.\n"
            f"Andra 'sensor_profile' i config.json.")

    profile = PROFILES[name]
    if optional is None:
        optional = optional_from_config()

    valbara = {s["id"] for s in profile["sensors"] if s.get("valfri")}
    okanda = set(optional) - valbara
    if okanda:
        raise SystemExit(
            f"config.json ber om givaren {', '.join(sorted(okanda))}, "
            f"men profilen {name} har ingen sadan valfri givare.\n"
            f"Valbara: {', '.join(sorted(valbara)) or 'inga'}.")

    monterade = [s for s in profile["sensors"]
                 if not s.get("valfri") or s["id"] in optional]
    return dict(profile, sensors=monterade)


# ---------------------------------------------------------------------------
# VIEWS OF THE MAP
# ---------------------------------------------------------------------------
# Small readers rather than one big object. Both the simulator and the poller
# build what they need through these, so neither can drift away from the
# table above.

def sensors(profile):
    """Every FITTED sensor in the profile, in bus address order."""
    return sorted(profile["sensors"], key=lambda s: s["unit"])


def optional_sensors(name=None):
    """{id: label} for the sensors a profile can be extended with.

    Read from the unfiltered table, so it lists what could be fitted -- not
    what is.
    """
    profile = PROFILES[name or profile_name_from_config()]
    return {s["id"]: s["label"] for s in profile["sensors"]
            if s.get("valfri")}


def value_specs(profile):
    """One flat row per measured VALUE: what the poller reads for the payload.

    A sensor that reports two quantities gives two rows with the same unit.
    The device-level fields (rail, warm-up, power) are repeated on each, so
    anything that adds up power has to count each unit once -- poller.py's
    rail_sensors() does that.
    """
    out = []
    for s in sensors(profile):
        for v in s["values"]:
            out.append({
                "unit": s["unit"],
                "sensor": s["id"],
                "sensor_label": s["label"],
                "modell": s["modell"],
                "key": v["key"],
                "label": v["label"],
                "addr": v["addr"],
                "scale": v["scale"],
                "signed": v["signed"],
                "resolution": 1.0 / v["scale"],
                "range": v["range"],
                "stuck_cycles": v.get("stuck_cycles"),
                "rail": s["rail"],
                "warmup_s": s["warmup_s"],
                "warmup_source": s["warmup_source"],
                "effekt_w": s.get("effekt_w"),
            })
    return out


def temp_specs(profile):
    """One flat row per sensor that also reports water temperature.

    Only one of them fills the payload's temp field. The others are read as
    a cross-check, see temp_d in poller.py.
    """
    out = []
    for s in sensors(profile):
        t = s.get("temp")
        if t is None:
            continue
        out.append({
            "unit": s["unit"],
            "sensor": s["id"],
            "label": f"temp {s['kort']}",
            "addr": t["addr"],
            "scale": t["scale"],
            "signed": t["signed"],
        })
    return out


def temp_primary_unit(profile):
    """Bus address of the sensor whose thermometer becomes the temp field."""
    want = profile["temp_primar"]
    for s in sensors(profile):
        if s["id"] == want and s.get("temp") is not None:
            return s["unit"]
    raise SystemExit(
        f"Profilen {profile['id']} pekar ut '{want}' som temperaturkalla, "
        f"men den givaren finns inte eller har ingen termometer.")


def value_registers(profile):
    """{key: (unit, addr, scale, signed)} -- the simulator's shape."""
    return {s["key"]: (s["unit"], s["addr"], s["scale"], s["signed"])
            for s in value_specs(profile)}


def temp_registers(profile):
    """{unit: (addr, scale, signed)}"""
    return {s["unit"]: (s["addr"], s["scale"], s["signed"])
            for s in temp_specs(profile)}


def warmup_times(profile):
    return {s["unit"]: s["warmup_s"] for s in sensors(profile)}


def warmup_sources(profile):
    return {s["unit"]: s["warmup_source"] for s in sensors(profile)}


def warmup_labels(profile):
    return {s["unit"]: s["kort"] for s in sensors(profile)}


def power_rails(profile):
    return {s["unit"]: s["rail"] for s in sensors(profile)}


def power_watts(profile):
    """{unit: (lowest W, highest W)} for the sensors that publish a figure.

    Sensors whose datasheet states nothing are absent, so anything worked out
    from this is incomplete and has to say which sensors it left out.
    """
    return {s["unit"]: s["effekt_w"] for s in sensors(profile)
            if s.get("effekt_w") is not None}


def power_sources(profile):
    return {s["unit"]: s.get("effekt_kalla", "ej angiven")
            for s in sensors(profile)}


def factory_addresses(profile):
    return {s["unit"]: s["factory_unit"] for s in sensors(profile)}


def address_registers(profile):
    return {s["unit"]: s["address_register"] for s in sensors(profile)}


def serial_settings(profile):
    """Bus parameters for this sensor family: baud rate, bits, parity, stops.

    Product specific, like the registers. It lives in the profile so that
    the poller and set_address.py can open a serial port without anybody
    writing 9600 into a second file. The PORT is not here: which tty the
    converter shows up on is a property of the machine, not of the sensors,
    so that stays in config.json.
    """
    return dict(profile["serie"])


def addressing_specs(profile):
    """One flat row per sensor for the commissioning job.

    Everything set_address.py needs to move a sensor from the address it
    shipped on to the address the buoy expects, plus a value register it
    reads back afterwards to prove the move worked.
    """
    out = []
    for s in sensors(profile):
        first = s["values"][0]
        out.append({
            "key": s["id"],
            "label": s["label"],
            "modell": s["modell"],
            "unit": s["unit"],                      # the address we want
            "factory_unit": s["factory_unit"],      # the address it ships on
            # Every address the sensor might ship on. Usually just the one;
            # more when the manual is not sure.
            "factory_candidates": list(s.get("factory_candidates",
                                             [s["factory_unit"]])),
            "address_register": s["address_register"],
            "adressbyte_not": s.get("adressbyte_not"),
            "value_addr": first["addr"],
            "scale": first["scale"],
            "signed": first["signed"],
            "warmup_s": s["warmup_s"],
            "kabel": s.get("kabel"),
            "matning": s.get("matning"),
        })
    return out


def raw_limits(profile):
    """{key: (lowest raw value, highest raw value)} where a range is known.

    Used by the simulator so a drifting sensor saturates at the end of its
    scale instead of reporting a value no real device can produce.
    Values whose datasheet states no range are simply absent here.
    """
    out = {}
    for s in value_specs(profile):
        span = s["range"]
        if span is None:
            continue
        out[s["key"]] = (int(round(span[0] * s["scale"])),
                         int(round(span[1] * s["scale"])))
    return out


# ---------------------------------------------------------------------------
# CONSISTENCY CHECK -- the thing that was missing before this file existed
# ---------------------------------------------------------------------------

def _simulator_view():
    import fake_sensors
    return {
        "vardesregister": {k: tuple(v)
                           for k, v in fake_sensors.REGISTERS.items()},
        "temperaturregister": {u: tuple(v)
                               for u, v in fake_sensors.TEMP_REGISTERS.items()},
        "uppvarmningstid": dict(fake_sensors.WARMUP_S),
        "strom-rail": dict(fake_sensors.POWER_RAIL),
        "temperaturkalla": fake_sensors.TEMP_SOURCE_UNIT,
    }


def _poller_view():
    import poller
    return {
        "vardesregister": {s["key"]: (s["unit"], s["addr"], s["scale"],
                                      s["signed"])
                           for s in poller.SENSORS},
        "temperaturregister": {s["unit"]: (s["addr"], s["scale"], s["signed"])
                               for s in poller.TEMP_SENSORS},
        "uppvarmningstid": {s["unit"]: s["warmup_s"] for s in poller.SENSORS},
        "strom-rail": {s["unit"]: s["rail"] for s in poller.SENSORS},
        "temperaturkalla": poller.TEMP_PRIMARY_UNIT,
    }


def _readers_on_path():
    """Make the simulator and the poller importable, when they exist at all.

    This file lives in delad/ and they live in laptop/, so neither is on the
    import path just because this self test is running. The directory the
    test is RUN from is where they sit, which is why the working directory
    goes on the path and not some hardcoded "../laptop".

    On the buoy neither of them exists. That is not a failure -- the firmware
    ships the profile and the poller, never the simulator.
    """
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def verify_consistency(log=print):
    """Fail loudly if the simulator and the poller disagree about the map.

    They both build their tables from this file, so a difference means
    somebody has written a register number back into one of them by hand.
    That is exactly the fault this file exists to make impossible, so it
    stops the program rather than printing a warning nobody reads.
    """
    _readers_on_path()
    try:
        sim = _simulator_view()
        poll = _poller_view()
    except ImportError as exc:
        # Nothing to compare against. Say which one is missing and from
        # where, so this reads as "not applicable" and never as "passed".
        log(f"  hoppar over: {exc.name} gar inte att importera fran "
            f"{os.getcwd()}")
        log("  (normalt pa bojen -- kor testet fran laptop/ for att jamfora)")
        return None

    fel = []
    for what in sim:
        if sim[what] != poll[what]:
            fel.append(what)
            log(f"  {what:<20} SKILJER SIG")
            log(f"      simulator: {sim[what]}")
            log(f"      pollare:   {poll[what]}")
        else:
            if isinstance(sim[what], dict):
                antal = len(sim[what])
                log(f"  {what:<20} OK   ({antal} "
                    f"{'post' if antal == 1 else 'poster'})")
            else:
                log(f"  {what:<20} OK   (enhet {sim[what]})")

    if fel:
        raise SystemExit(
            "\nKARTAN SKILJER SIG MELLAN SIMULATOR OCH POLLARE.\n"
            f"Avviker: {', '.join(fel)}.\n"
            "Bada ska bygga sin karta ur sensor_profiles.py. Nagon har "
            "skrivit in ett registernummer for hand igen.")

    log("\n  Bada laser sensor_profiles.py. Ingen av dem har en egen kopia.")
    return True


# ---------------------------------------------------------------------------
# SELF TEST -- prints the map and checks that both readers agree about it
# ---------------------------------------------------------------------------

def selftest():
    profile = load_profile()

    print(f"Sensorprofil: {profile['id']}\n")
    print(f"  {profile['namn']}")
    print(f"  Verifierad {profile['verifierad']} ({profile['kalla']})")
    s = profile["serie"]
    print(f"  Buss: {s['baudrate']} baud {s['bytesize']}"
          f"{s['parity']}{s['stopbits']}")

    valbara = optional_sensors()
    if valbara:
        monterade = {s["id"] for s in sensors(profile)}
        print("\n  Valfria givare (monteras bara ibland)")
        for key, label in sorted(valbara.items()):
            status = ("MONTERAD" if key in monterade
                      else f"ej monterad -- lagg '{key}' i optional_sensors")
            print(f"    {key:<6} {label:<16} {status}")

    print("\nGivarna\n")
    print(f"  {'adr':>3} {'givare':<18} {'modell':<19} {'rail':<5} "
          f"uppvarmning")
    for sensor in sensors(profile):
        print(f"  {sensor['unit']:>3} {sensor['label']:<18} "
              f"{sensor['modell']:<19} {sensor['rail']:<5} "
              f"{sensor['warmup_s']:>4.0f} s  {sensor['warmup_source']}")

    print("\nRegisterkartan, ett falt per matvarde\n")
    print(f"  {'adr':>3} {'falt':<5} {'storhet':<12} {'register':>8} "
          f"{'skala':>6} {'typ':<10} matomrade")
    for spec in value_specs(profile):
        typ = "signerad" if spec["signed"] else "osignerad"
        if spec["range"] is None:
            omrade = "anges inte i databladet"
        else:
            omrade = f"{spec['range'][0]:g} till {spec['range'][1]:g}"
        print(f"  {spec['unit']:>3} {spec['key']:<5} {spec['label']:<12} "
              f"  0x{spec['addr']:04X} {spec['scale']:>6} {typ:<10} {omrade}")

    print(f"\n  Temperaturregister ({len(temp_specs(profile))} av "
          f"{len(sensors(profile))} givare)\n")
    primar = temp_primary_unit(profile)
    for spec in temp_specs(profile):
        mark = "  <- fyller faltet temp" if spec["unit"] == primar else ""
        typ = "signerad" if spec["signed"] else "osignerad"
        print(f"  {spec['unit']:>3} {spec['label']:<20} 0x{spec['addr']:04X} "
              f"{spec['scale']:>6} {typ:<10}{mark}")
    if len(temp_specs(profile)) >= 2:
        print(f"\n  Tva termometrar: temp_d skickas, och over "
              f"{TEMP_D_TOLERANCE_C:g} C ar de oense")
        print("  (databladens noggrannhet, +-0,5 och +-0,3 C, tillsammans).")

    print("\n  Register som finns men INTE lases\n")
    for sensor in sensors(profile):
        for addr, vad, varfor in sensor["skipped"]:
            print(f"  {sensor['unit']:>3} {sensor['kort']:<11} "
                  f"0x{addr:04X}  {vad:<18} {varfor}")

    print("\n  Stromkanaler, effekt och matning\n")
    for rail in ("fast", "slow"):
        pa = [s for s in sensors(profile) if s["rail"] == rail]
        if not pa:
            continue
        varmast = max(s["warmup_s"] for s in pa)
        namn = ", ".join(s["label"] for s in pa)
        print(f"  {rail:<5} {len(pa)} givare  klar efter {varmast:>4.0f} s   "
              f"{namn}")
    print()
    for sensor in sensors(profile):
        effekt = sensor.get("effekt_w")
        kalla = sensor.get("effekt_kalla", "ej angiven")
        if effekt is None:
            text = "effekt okand"
        elif effekt[0] == effekt[1]:
            text = f"{effekt[0]:.2f} W"
        else:
            text = f"{effekt[0]:.2f}-{effekt[1]:.2f} W"
        print(f"  {sensor['unit']:>3} {sensor['label']:<18} {text:<10} "
              f"{kalla}")
        print(f"      {'':<18} matning {sensor.get('matning', 'ej angiven')}")

    print("\n  Fabriksadresser och adressregister "
          f"-- {profile['adressregister_status']}\n")
    fabrik = factory_addresses(profile)
    delade = [a for a in fabrik.values() if list(fabrik.values()).count(a) > 1]
    for sensor in sensors(profile):
        unit = sensor["unit"]
        krock = "  <- KROCKAR" if fabrik[unit] in delade else ""
        print(f"  {unit:>3} {sensor['label']:<18} fabriksadress "
              f"{fabrik[unit]:>3}   andras via "
              f"0x{address_registers(profile)[unit]:04X}{krock}")
        if sensor.get("adressbyte_not"):
            print(f"      {sensor['adressbyte_not']}")

    print("\n  Kabelvarningar\n")
    for sensor in sensors(profile):
        if sensor["kabel"]:
            print(f"  {sensor['unit']:>3} {sensor['label']:<18} "
                  f"{sensor['kabel']}")

    print("\nKartkontroll: ser simulator och pollare samma karta?\n")
    verify_consistency()


if __name__ == "__main__":
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        selftest()
