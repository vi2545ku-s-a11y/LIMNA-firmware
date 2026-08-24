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

The family below is the one we have datasheets for (Seeed SenseCAP, verified
2026-07-29 in sensor_databla.md). It is NOT bought yet. The final choice may
be a different family altogether, for instance B&C Electronics 8325, and then
almost nothing in the table survives:

    portable        the operating addresses 1-6. We set those ourselves at
                    assembly time and they do not depend on the make.
    product         factory addresses, register addresses, scale factors,
    specific        signed or unsigned, measuring ranges, warm-up times,
                    the address-change register and the cable colours.

HOW TO ADD A NEW FAMILY

    1. Copy SEEED_2026 below, give it a new id and fill it in FROM THE
       DATASHEETS. Name the datasheet in "source".
    2. Add it to PROFILES.
    3. Point config.json at it:   "sensor_profile": "din-nya-profil"
    4. Run  python3 sensor_profiles.py --selftest  and read the printed map.

That is the whole change. Nothing in fake_sensors.py or poller.py should need
touching to swap families.

WHAT TO VERIFY AGAINST THE DATASHEET BEFORE USING A NEW PROFILE

Every single one of these has either bitten this project already or is known
to differ between manufacturers:

    addr        the register address of the value itself.
    scale       the implied divisor. Ours range from 1 to 100 in the SAME
                family -- do not assume one number for all sensors.
    signed      a Modbus register is 16 bits and it is the READER that has to
                know whether they are two's complement. Read a signed one as
                unsigned and -2.51 C comes back as 652.85 C, which looks
                perfectly plausible in a log.
    scale_register  some sensors do not have a fixed scale at all. Ours
                stores the number of decimals in a register of its own.
    range       the measuring range, used to saturate the simulator and to
                flag impossible readings. Leave it None when the datasheet
                does not state one; never invent it.
    factory_unit    the address the sensor ships on. Two of ours ship on the
                same one, which is a wiring trap, not a code problem.
    address_register    where the new address is written. OVERIFIERAT for
                this family, see the note in SEEED_2026.
    warmup_s / warmup_source    how long after power-up the reading can be
                trusted, and WHO SAYS SO. This number drives the energy
                budget, so a guess must go in as "gissning" and never as a
                bare number.
    rail        which switched power channel the sensor hangs off. The fast
                group can be read and cut while the slow group settles
                (requirements E-11 and E-12).
    effekt_w    power draw as (lowest, highest) watts at the buoy's 12 V, or
                None when the manufacturer does not publish one. A span
                rather than a number because ours are stated at 24 V and the
                conversion depends on the converter type. Used only to put a
                figure on what a split rail would save -- never to decide
                anything on its own.
    skipped     registers the sensor offers that we deliberately do not read.
                Written down so the next reader knows they were considered
                and rejected, not missed.
    stuck_cycles    how many measurements in a row this sensor's value may be
                bit-identical before it is suspected of being frozen. It
                belongs here and not in config.json because it follows from
                the RESOLUTION of the register: a value quantised to whole
                units can honestly repeat for hours in still water, one
                quantised to hundredths cannot.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# WHICH config.json THIS FILE READS
#
# Only two things are asked of it: which profile is fitted, and which optional
# sensors. But this file now lives in delad/ and is shared with the firmware,
# while config.json belongs to the program that runs -- laptop/config.json for
# the rehearsal, and something else entirely on the buoy. Guessing "next to
# myself" would look for delad/config.json, which nothing ever writes.
#
# So the caller says. poller.py and fake_sensors.py both call use_config()
# before loading a profile. The default below only matters when this file is
# run on its own, as its own self test.
CONFIG_FILE = os.path.join(HERE, "config.json")


def use_config(path):
    """Point the profile loader at the config the running program uses.

    Called by whoever owns the config file, so that the profile and the
    poller can never end up reading two different ones.
    """
    global CONFIG_FILE
    CONFIG_FILE = path

DEFAULT_PROFILE = "seeed-2026"


# ---------------------------------------------------------------------------
# PROFILE: Seeed SenseCAP, the five sensors we have datasheets for
# ---------------------------------------------------------------------------
# Verified against the manufacturers' datasheets 2026-07-29, written up in
# sensor_databla.md. Do not "tidy" the inconsistencies and do not change a
# number without a datasheet in hand. That turbidity scales by 10 while
# oxygen scales by 100 and conductivity by 1 is true, not sloppy.

SEEED_2026 = {
    "id": "seeed-2026",
    "namn": "Seeed SenseCAP, femsensorsuppsattningen 2026",
    "verifierad": "2026-07-29",
    "kalla": "sensor_databla.md",

    # 9600 8N1 for all five. Also product specific -- another family may not
    # agree with itself here either.
    "serie": {"baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1},

    # Which sensor's thermometer fills the payload's temp field. By payload
    # key, not by address, so it survives a re-addressing.
    "temp_primar": "ec",

    # The register the new bus address is written to. sensor_databla.md gives
    # three different registers for the five sensors; README and the old code
    # claimed 0x0200 for all of them. Nobody has tried it on hardware, so
    # every address_register below is OVERIFIERAT. Writing to the wrong one
    # does nothing at best and reconfigures something else at worst -- check
    # against the datasheet page before the assembly day, not during it.
    "adressregister_status": "OVERIFIERAT mot hardvara",

    "sensors": [
        {
            "key": "do",
            "stuck_cycles": 24,
            "label": "loest syre",
            "kort": "syre",
            "modell": "S-RJY-01",
            "unit": 1,
            "factory_unit": 55,
            "address_register": 0x2000,
            "value": {"addr": 0x0101, "scale": 100, "signed": False},
            "temp": {"addr": 0x0100, "scale": 10, "signed": False},
            "range": None,          # not stated in the datasheet
            "warmup_s": 40.0,
            "warmup_source": "gissning",
            "rail": "slow",
            "effekt_w": (0.30, 0.30),
            "effekt_kalla": "datablad, 0,3 W @ 12 V",
            "skipped": [
                (0x0102, "mattnad %", "harleds ur syre och temperatur"),
            ],
            "kabel": "bla = RS485-A",
        },
        {
            "key": "turb",
            "stuck_cycles": 24,
            "label": "turbiditet",
            "kort": "turbiditet",
            "modell": "S-DTS210-01",
            "unit": 2,
            "factory_unit": 50,
            "address_register": 0x2000,
            "value": {"addr": 0x0101, "scale": 10, "signed": False},
            "temp": {"addr": 0x0100, "scale": 10, "signed": False},
            "range": None,
            "warmup_s": 8.0,
            "warmup_source": "gissning",
            "rail": "slow",
            "effekt_w": (0.30, 0.30),
            "effekt_kalla": "datablad, 0,3 W @ 12 V",
            "skipped": [],
            "kabel": None,
        },
        {
            "key": "ec",
            # 1 uS/cm upplosning mot en naturlig variation pa nagra
            # enheter: konduktiviteten kan arligt sta pa samma heltal
            # i timmar i stilla vatten. 96 cykler = ett dygn.
            "stuck_cycles": 96,
            "label": "konduktivitet",
            "kort": "konduktivitet",
            "modell": "S-EC-01",
            "unit": 3,
            "factory_unit": 1,      # collides with pH, see selftest
            "address_register": 0x0200,
            "value": {"addr": 0x0002, "scale": 1, "signed": False},
            "temp": {"addr": 0x0000, "scale": 100, "signed": True},
            "range": None,
            "warmup_s": 2.0,
            "warmup_source": "datablad",
            "rail": "fast",
            # 40 mA vila, 80 mA max, bada angivna vid 24 V. Vid 12 V drar en
            # switchad omvandlare samma EFFEKT och en linjar samma STROM --
            # det gar inte att avgora ur databladet, darav spannet. Mat.
            "effekt_w": (0.48, 1.92),
            "effekt_kalla": "datablad, 40-80 mA @ 24 V, 12 V-effekten omatt",
            "skipped": [
                (0x0003, "salthalt mg/L", "harleds ur konduktiviteten"),
                (0x0004, "TDS mg/L", "harleds ur konduktiviteten"),
            ],
            "kabel": None,
        },
        {
            "key": "ph",
            "stuck_cycles": 24,
            "label": "pH",
            "kort": "pH",
            "modell": "S-pH-01A",
            "unit": 4,
            "factory_unit": 1,      # collides with conductivity
            "address_register": 0x0200,
            "value": {"addr": 0x0001, "scale": 100, "signed": False},
            "temp": {"addr": 0x0000, "scale": 100, "signed": True},
            "range": (0.0, 14.0),   # datasheet: register 0-1400
            "warmup_s": 2.0,
            "warmup_source": "datablad",
            "rail": "fast",
            "effekt_w": (0.84, 0.84),
            "effekt_kalla": "datablad, 35 mA @ 24 V",
            "skipped": [],
            "kabel": "A = gul. BLA ar 4-20 mA-utgang har, inte RS485-A.",
        },
        {
            # U-04: a SECOND oxygen sensor, identical to the first, fitted
            # only during the field test. The point is not a second reading
            # of the oxygen -- it is the GAP between the two. Biological
            # fouling grows on the optical window and makes a sensor read
            # low while it keeps answering perfectly, which no threshold,
            # rate or silence alarm can see. Two of them in the same water
            # turn that into a number, do_d, the same way temp_d does for
            # the four thermometers.
            #
            # Optional: valfri means it is left out unless config.json asks
            # for it in "optional_sensors". The buoy is not built with six
            # sensors -- address 6 is reserved for the trial.
            "valfri": True,
            "key": "do2",
            "stuck_cycles": 24,
            "label": "loest syre 2",
            "kort": "syre 2",
            "modell": "S-RJY-01",
            "unit": 6,
            # Same model as unit 1, so it ships on the same factory address.
            # The two of them collide with EACH OTHER exactly as conductivity
            # and pH do -- address them one at a time.
            "factory_unit": 55,
            "address_register": 0x2000,
            "value": {"addr": 0x0101, "scale": 100, "signed": False},
            # THIS SENSOR'S THERMOMETER IS NOT READ, even though it has one.
            #
            # temp_d is the spread between FOUR thermometers and its 1.0 C
            # threshold was set against four: their calibration errors give
            # about 0.2 C of spread, and 1.0 C means one of them is drifting.
            # A fifth reading makes the spread systematically larger, so the
            # same threshold would no longer mean the same thing -- and the
            # threshold would then differ between field test and normal
            # operation, which is worse than not having the extra reading.
            #
            # The register is there if somebody later wants it. Reading it
            # would cost bus time and current for a number that would make an
            # existing check less trustworthy.
            "temp": None,
            "range": None,
            "warmup_s": 40.0,
            "warmup_source": "gissning",
            "rail": "slow",
            "effekt_w": (0.30, 0.30),
            "effekt_kalla": "datablad, 0,3 W @ 12 V",
            "skipped": [
                (0x0102, "mattnad %", "harleds ur syre och temperatur"),
                (0x0100, "temperatur", "temp_d ar satt for fyra givare"),
            ],
            "kabel": "bla = RS485-A",
        },
        {
            "key": "lvl",
            "stuck_cycles": 24,
            "label": "niva",
            "kort": "niva",
            "modell": "S-YW-01B",
            "unit": 5,
            "factory_unit": 26,
            "address_register": 0x0000,
            # scale None means the scale is not fixed: the sensor stores the
            # number of decimals in scale_register and the real divisor is
            # 10 ** that. Read it once at start-up.
            "value": {"addr": 0x0004, "scale": None, "signed": True,
                      "scale_register": 0x0003},
            "temp": None,           # this one has no thermometer
            "range": None,
            "warmup_s": 1.0,
            "warmup_source": "harledd",   # from a 5 ms RESPONSE time, which
                                          # is not the same as a start-up time
            "rail": "fast",
            # The only sensor of the five with no published figure at all.
            # Every energy number that includes the level probe is therefore
            # incomplete, and says so rather than guessing.
            "effekt_w": None,
            "effekt_kalla": "ej publicerad",
            "skipped": [
                (0x0002, "enhet", "konstant 16 = meter"),
            ],
            "kabel": None,
        },
    ],
}


PROFILES = {
    SEEED_2026["id"]: SEEED_2026,
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

    Sensors marked "valfri" are left out unless they are named in
    optional_sensors. The second oxygen sensor of requirement U-04 is one:
    it is fitted for the field test and taken off again afterwards, and the
    buoy must run identically either way.
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

    valbara = {s["key"] for s in profile["sensors"] if s.get("valfri")}
    okanda = set(optional) - valbara
    if okanda:
        raise SystemExit(
            f"config.json ber om givaren {', '.join(sorted(okanda))}, "
            f"men profilen {name} har ingen sadan valfri givare.\n"
            f"Valbara: {', '.join(sorted(valbara)) or 'inga'}.")

    monterade = [s for s in profile["sensors"]
                 if not s.get("valfri") or s["key"] in optional]
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
    """{key: label} for the sensors a profile can be extended with.

    Read from the unfiltered table, so it lists what could be fitted -- not
    what is.
    """
    profile = PROFILES[name or profile_name_from_config()]
    return {s["key"]: s["label"] for s in profile["sensors"]
            if s.get("valfri")}


def value_specs(profile):
    """One flat row per sensor: what the poller reads for the payload."""
    out = []
    for s in sensors(profile):
        v = s["value"]
        out.append({
            "unit": s["unit"],
            "key": s["key"],
            "label": s["label"],
            "addr": v["addr"],
            "scale": v["scale"],
            "signed": v["signed"],
            "scale_register": v.get("scale_register"),
            "rail": s["rail"],
            "warmup_s": s["warmup_s"],
            "warmup_source": s["warmup_source"],
            "range": s["range"],
            "effekt_w": s.get("effekt_w"),
            "stuck_cycles": s.get("stuck_cycles"),
        })
    return out


def temp_specs(profile):
    """One flat row per sensor that also reports water temperature.

    Four of the five do. Only one of them fills the payload's temp field --
    the rest are read as a cross-check, see temp_d in poller.py.
    """
    out = []
    for s in sensors(profile):
        t = s.get("temp")
        if t is None:
            continue
        out.append({
            "unit": s["unit"],
            "key": s["key"],
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
        if s["key"] == want and s.get("temp") is not None:
            return s["unit"]
    raise SystemExit(
        f"Profilen {profile['id']} pekar ut '{want}' som temperaturkalla, "
        f"men den givaren finns inte eller har ingen termometer.")


def value_registers(profile):
    """{unit: (addr, scale, signed, key)} -- the simulator's own shape."""
    return {s["unit"]: (s["addr"], s["scale"], s["signed"], s["key"])
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

    Product specific, like the registers -- another family may not even agree
    with itself here. It lives in the profile so that the poller and
    set_address.py can open a serial port without anybody writing 9600 into a
    second file. The PORT is not here: which tty the converter shows up on is
    a property of the machine, not of the sensors, so that stays in
    config.json.
    """
    return dict(profile["serie"])


def addressing_specs(profile):
    """One flat row per sensor for the commissioning job.

    Everything set_address.py needs to move a sensor from the address it
    shipped on to the address the buoy expects, plus the value register it
    reads back afterwards to prove the move worked.
    """
    varden = {s["unit"]: s for s in value_specs(profile)}
    out = []
    for s in sensors(profile):
        v = varden[s["unit"]]
        out.append({
            "key": s["key"],
            "label": s["label"],
            "modell": s["modell"],
            "unit": s["unit"],                      # the address we want
            "factory_unit": s["factory_unit"],      # the address it ships on
            "address_register": s["address_register"],
            "value_addr": v["addr"],
            "scale": v["scale"],
            "signed": v["signed"],
            "warmup_s": s["warmup_s"],
            "kabel": s.get("kabel"),
        })
    return out


def raw_limits(profile):
    """{unit: (lowest raw value, highest raw value)} where a range is known.

    Used by the simulator so a drifting sensor saturates at the end of its
    scale instead of reporting pH 15.37, which no real device can do.
    Sensors whose datasheet states no range are simply absent here.
    """
    out = {}
    for s in sensors(profile):
        span = s["range"]
        scale = s["value"]["scale"]
        if span is None or scale is None:
            continue
        out[s["unit"]] = (int(round(span[0] * scale)),
                          int(round(span[1] * scale)))
    return out


def scale_register_spec(profile):
    """The one sensor whose scale has to be asked for, or None.

    Ours is the level probe: register 0x0003 holds the number of decimals.
    Another family may have no such sensor at all.
    """
    for s in value_specs(profile):
        if s["scale_register"] is not None:
            return s
    return None


# ---------------------------------------------------------------------------
# CONSISTENCY CHECK -- the thing that was missing before this file existed
# ---------------------------------------------------------------------------

def _simulator_view():
    import fake_sensors
    return {
        "vardesregister": {u: tuple(v)
                           for u, v in fake_sensors.REGISTERS.items()},
        "temperaturregister": {u: tuple(v)
                               for u, v in fake_sensors.TEMP_REGISTERS.items()},
        "uppvarmningstid": dict(fake_sensors.WARMUP_S),
        "strom-rail": dict(fake_sensors.POWER_RAIL),
        "temperaturkalla": fake_sensors.TEMP_SOURCE_UNIT,
    }


def _poller_view():
    import poller
    return {
        "vardesregister": {s["unit"]: (s["addr"], s["scale"], s["signed"],
                                       s["key"]) for s in poller.SENSORS},
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
            antal = len(sim[what]) if isinstance(sim[what], dict) else 1
            log(f"  {what:<20} OK   ({antal} poster)"
                if antal > 1 else f"  {what:<20} OK   (enhet {sim[what]})")

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
    print(f"  Verifierad mot datablad {profile['verifierad']} "
          f"({profile['kalla']})")
    s = profile["serie"]
    print(f"  Buss: {s['baudrate']} baud {s['bytesize']}"
          f"{s['parity']}{s['stopbits']}")

    valbara = optional_sensors()
    if valbara:
        monterade = {s["key"] for s in sensors(profile)}
        print("\n  Valfria givare (monteras bara ibland)")
        for key, label in sorted(valbara.items()):
            status = ("MONTERAD" if key in monterade
                      else f"ej monterad -- lagg '{key}' i optional_sensors")
            print(f"    {key:<6} {label:<16} {status}")

    print("\nRegisterkartan\n")
    print(f"  {'adr':>3} {'givare':<14} {'modell':<12} {'register':>8} "
          f"{'skala':>6} {'typ':<10} {'rail':<5} uppvarmning")
    for spec in value_specs(profile):
        skala = "ur giv." if spec["scale"] is None else str(spec["scale"])
        typ = "signerad" if spec["signed"] else "osignerad"
        modell = next(x["modell"] for x in sensors(profile)
                      if x["unit"] == spec["unit"])
        kalla = warmup_sources(profile)[spec["unit"]]
        print(f"  {spec['unit']:>3} {spec['label']:<14} {modell:<12} "
              f"0x{spec['addr']:04X} {skala:>8} {typ:<10} "
              f"{spec['rail']:<5} {spec['warmup_s']:>4.0f} s  {kalla}")

    print("\n  Temperaturregister (fyra av fem givare)\n")
    primar = temp_primary_unit(profile)
    for spec in temp_specs(profile):
        mark = "  <- fyller faltet temp" if spec["unit"] == primar else ""
        typ = "signerad" if spec["signed"] else "osignerad"
        print(f"  {spec['unit']:>3} {spec['label']:<20} 0x{spec['addr']:04X} "
              f"{spec['scale']:>6} {typ:<10}{mark}")

    print("\n  Register som finns men INTE lases\n")
    for sensor in sensors(profile):
        for addr, vad, varfor in sensor["skipped"]:
            print(f"  {sensor['unit']:>3} {sensor['label']:<14} "
                  f"0x{addr:04X}  {vad:<16} {varfor}")

    print("\n  Stromkanaler och effekt\n")
    for rail in ("fast", "slow"):
        pa = [s for s in value_specs(profile) if s["rail"] == rail]
        if not pa:
            continue
        varmast = max(s["warmup_s"] for s in pa)
        namn = ", ".join(s["label"] for s in pa)
        print(f"  {rail:<5} {len(pa)} givare  klar efter {varmast:>4.0f} s   "
              f"{namn}")
    print()
    for spec in value_specs(profile):
        effekt = spec["effekt_w"]
        kalla = power_sources(profile)[spec["unit"]]
        if effekt is None:
            print(f"  {spec['unit']:>3} {spec['label']:<14} "
                  f"{'effekt okand':<16} {kalla}")
        elif effekt[0] == effekt[1]:
            print(f"  {spec['unit']:>3} {spec['label']:<14} "
                  f"{effekt[0]:>6.2f} W        {kalla}")
        else:
            print(f"  {spec['unit']:>3} {spec['label']:<14} "
                  f"{effekt[0]:.2f}-{effekt[1]:.2f} W    {kalla}")

    print("\n  Matomraden\n")
    for spec in value_specs(profile):
        if spec["range"] is None:
            print(f"  {spec['unit']:>3} {spec['label']:<14} "
                  f"anges inte i databladet")
        else:
            low, high = spec["range"]
            rlow, rhigh = raw_limits(profile)[spec["unit"]]
            print(f"  {spec['unit']:>3} {spec['label']:<14} "
                  f"{low:g} till {high:g}   (register {rlow}-{rhigh})")

    print("\n  Fabriksadresser och adressregister "
          f"-- {profile['adressregister_status']}\n")
    fabrik = factory_addresses(profile)
    delade = [a for a in fabrik.values() if list(fabrik.values()).count(a) > 1]
    for spec in value_specs(profile):
        unit = spec["unit"]
        krock = "  <- KROCKAR" if fabrik[unit] in delade else ""
        print(f"  {unit:>3} {spec['label']:<14} fabriksadress "
              f"{fabrik[unit]:>3}   andras via "
              f"0x{address_registers(profile)[unit]:04X}{krock}")

    print("\n  Kabelvarningar\n")
    for sensor in sensors(profile):
        if sensor["kabel"]:
            print(f"  {sensor['unit']:>3} {sensor['label']:<14} "
                  f"{sensor['kabel']}")

    print("\nKartkontroll: ser simulator och pollare samma karta?\n")
    verify_consistency()


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        selftest()
