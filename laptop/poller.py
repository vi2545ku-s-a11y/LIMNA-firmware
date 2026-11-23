#!/usr/bin/env python3
"""
LIMNA - measurement poller.

This is a laptop rehearsal of the firmware that will run on the buoy. The
sequence is deliberately the same one the ESP32 will follow:

    power on the sensor rail  ->  wait out the warm-up  ->  read each sensor
    ->  cut power  ->  timestamp  ->  write to local storage  ->  try to send

Everything that is hard to get right lives here, not in the sensors:
retries, partial failure, local buffering, catching up after an outage, and
surviving a fault instead of dying of it. Those map to requirements EL-03,
EL-05, K-03 and K-04.

    python3 poller.py

Requires:  pip install pymodbus paho-mqtt
"""

import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone


# sensor_profiles.py ligger i delad/, en niva upp, eftersom firmwaren pa
# ESP32:n ska lasa exakt samma registerkarta. Tre rader har i stallet for en
# kopia per mapp -- en kopia ar precis det felet profilen finns for att
# forhindra.
DELAD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     os.pardir, "delad")
if DELAD not in sys.path:
    sys.path.insert(0, DELAD)

import sensor_profiles

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")
DB_FILE = os.path.join(HERE, "buffer.db")

# The profile answers "which family is fitted?" out of the same config file
# this program writes, not one it guessed at.
sensor_profiles.use_config(CONFIG_FILE)

DEFAULT_CONFIG = {
    # Which buoy this is. The name is what the farmer sees in the dashboard.
    "buoy_id": "LIMNA-001",
    "buoy_name": "Bevattningsdamm test",

    # WHICH BUS THE SENSORS HANG OFF.
    #
    # "tcp" is the laptop rehearsal: fake_sensors.py answers on localhost.
    # "rtu" is the buoy: real Modbus over an RS485 converter on a serial port.
    # Changing this is a config edit and nothing else -- Modbus is Modbus, and
    # every register, scale and retry above works the same either way.
    #
    # Baud rate, parity, bits and stop bits are NOT here. They belong to the
    # sensor family, so they come from the profile ("serie" in
    # sensor_profiles.py) -- the same place the registers come from. Which tty
    # the converter turns up on is a property of this machine and not of the
    # sensors, so serial_port is here.
    "modbus": {"transport": "tcp",
               "host": "127.0.0.1", "port": 5020,
               "serial_port": "/dev/ttyUSB0",
               "timeout_s": 1.0},
    # K-02: tls off and no credentials is the laptop setup, deliberately.
    # See the comment above flush() for what turning it on requires.
    "mqtt": {"host": "127.0.0.1", "port": 1883,
             "topic": "limna/LIMNA-001/measurements",
             "tls": {"enabled": False, "ca_cert": None, "insecure": False},
             "username_env": "LIMNA_MQTT_USER",
             "password_env": "LIMNA_MQTT_PASS"},

    # Which sensor family is fitted. The register map for it lives in
    # sensor_profiles.py -- swapping family means naming another profile
    # here, not editing this file.
    "sensor_profile": sensor_profiles.DEFAULT_PROFILE,

    "measure_interval_s": 60,
    "send_interval_s": 120,

    # Eco mode (F-02): what the intervals become when the battery is low.
    # larmregler.py reads eco_send_interval_s from here too -- the silence
    # rule has to be built for the slowest mode the buoy can end up in.
    "eco_send_interval_s": 21600,

    # ECO THRESHOLDS ARE NOT THE ALARM THRESHOLD. This was built wrong first
    # time round: eco mode entered at 12.4 V, the same voltage as the low
    # battery alarm in F-13, so the buoy started saving power at the exact
    # moment the situation became urgent. Eco mode exists to postpone that
    # moment, and it was given no room to work in.
    #
    # 4S LiFePO4: the discharge curve is flat down to roughly 12.8 V and then
    # falls off a cliff. Half capacity sits near 13.0 V at rest; 12.4 V is
    # down around ten per cent. Berakningsunderlaget 5.2 gets its ~30 days of
    # autonomy by assuming the switch happens at half the battery -- so
    # 13.0 V in, 13.2 V out, and 12.4 V stays what it always was: the alarm
    # that says a service visit is due.
    "eco_mode": {
        "enabled": True,
        "enter_v": 13.0,        # half capacity on 4S LiFePO4, at rest
        "exit_v": 13.2,         # hysteresis, so it does not flap on the limit
        "measure_interval_s": 3600,
    },

    # Battery (E-07). "fixed" reports volt unchanged, which is what the buoy
    # did before there was any source at all. "sim" drains from volt at
    # drain_v_per_h so that eco mode and the low battery alarm can be
    # provoked on a laptop. On the buoy both are replaced by one ADC reading.
    "battery": {
        "source": "fixed",
        "volt": 13.1,
        "drain_v_per_h": 0.0,
        "floor_v": 10.8,
    },

    # 30 s follows the pH probe's stated ORP response time, the slowest
    # figure either datasheet gives. It is still an assumption about
    # START-UP, not a measurement -- measure it first.
    "warmup_s": 30,
    "retries": 3,

    # F-12: the limits the BUOY checks for itself, so it can tell an alarming
    # measurement from an ordinary one and send it at once instead of at the
    # next window. null means no limit in that direction.
    #
    # The same limits also live in Grafana, and that duplication is
    # deliberate: the two do different jobs. The buoy decides WHEN to send;
    # the server decides who to wake up, keeps the alarm history and catches
    # the rate-of-change and silence cases the buoy cannot see on its own.
    # Neither replaces the other -- if the buoy is wrong the server still
    # alarms, and if the server is down the measurement still went out early.
    "alarm_limits": {
        "_krav": ("Larmnivaerna bestams tillsammans med bonden, for den "
                  "faktiska dammen. Talen nedan ar inte vara att satta."),
        "_placeholder": ("PLACEHOLDER -- ersatts fore pilot: ph, orp och "
                         "turb. Endast batt ar beslutad (samma varde som "
                         "larmregeln i Grafana)."),

        # Bevattningsvatten trivs ungefar mellan pH 6,5 och 8,4. Over 9
        # fastnar kalk i droppslangar och vissa bekampningsmedel tappar
        # verkan; under 6 angrips metall. Talen ar en startpunkt, inte
        # dammens granser.
        "ph": {"low": 6.0, "high": 9.0},

        # Ingen grans an. Lagt eller negativt ORP betyder syrefritt vatten,
        # men var gransen gar beror pa dammen. Satts pa plats.
        "orp": {"low": None, "high": None},

        # Grumligt vatten satter igen filter och droppare. 50 NTU ar en
        # startpunkt; regn ger tillfalliga toppar over den.
        "turb": {"low": None, "high": 50.0},

        "temp": {"low": None, "high": None},
        "batt": {"low": 12.4, "high": None},
    },

    # Databehandling per matning. Se den langa kommentaren ovanfor
    # sample_sensor() for varfor det ar tre avlasningar och en median.
    "sampling": {
        "reads_per_measurement": 3,
        # Median, inte medelvarde. Ett enda skrapvarde flyttar ett
        # medelvarde men inte en median. "mean" finns for jamforelse.
        "method": "median",
        # Hur mycket de tre far skilja sig innan raden flaggas "brusig",
        # som andel av vardet. 0.10 ar ett ANTAGANDE -- en frisk givare
        # laser tre ganger inom nagon promille, sa tio procent ar generost.
        "spread_warn_frac": 0.10,
        # Flagga varden utanfor databladets matomrade. Vardet sparas anda.
        "range_check": True,
    },

    # How many measurements in a row a value may be bit-identical before it
    # is flagged as suspected frozen. 24 at the real 15 minute interval is
    # six hours; at the 60 s development interval it is twenty-four minutes,
    # which is short enough to actually test.
    "stuck_cycles": 24,

    "_note": ("measure_interval_s is 60 here so you see results quickly. "
              "The real buoy uses 900 (15 min). warmup_s 30 is the pH "
              "probe's ORP response time, not a measured start-up time -- "
              "measure the real one and correct it here first of all."),
}

# THE REGISTER MAP COMES FROM sensor_profiles.py
#
# It used to be written out here as well as in the simulator, and the two
# could drift apart without anything noticing. Now both read the same table:
# which sensor family is fitted is a line in config.json, and
# sensor_profiles.py --selftest checks that the two still see the same map.
#
# Do not write a register number back into this file. That is exactly the
# fault the profile exists to prevent.
PROFILE = sensor_profiles.load_profile()

# One row per measured VALUE: unit, key, label, addr, scale, signed,
# warmup_s, range. The pH probe gives two rows, pH and ORP, with the same
# unit -- it is one device answering for two quantities.
SENSORS = sensor_profiles.value_specs(PROFILE)

# THE THERMOMETERS
#
# Every sensor that has a thermometer is read. Only one number goes in the
# payload, from the sensor the profile names in temp_primar. The others are
# read as a CROSS-CHECK, not as extra data. In the pond profile both sensors
# have one, so the cross-check below is live.
#
# Two instruments in the same water should agree to within their calibration
# error, a few tenths of a degree. When one walks away from the other, that
# sensor's board is drifting -- and drift is the failure that nothing else
# here catches, because a drifting sensor keeps answering and keeps looking
# plausible. It does not stop working. It starts lying.
#
# So once there are two or more, the payload carries one extra number,
# temp_d: the largest gap between any two of them.
TEMP_SENSORS = sensor_profiles.temp_specs(PROFILE)
TEMP_PRIMARY_UNIT = sensor_profiles.temp_primary_unit(PROFILE)

# ---------------------------------------------------------------------------
# WHAT A MEASUREMENT COSTS IN ENERGY
# ---------------------------------------------------------------------------
# The sensor rail is on for the whole warm-up, and the warm-up is the largest
# item in the buoy's energy budget. So every cycle logs how long the rail was
# on and what that cost, from the datasheets' power figures. Both figures are
# upper bounds, so the cost is too, until the currents are measured.

def sensor_power_w():
    """Total power of all sensors, counting each device once.

    SENSORS has one row per VALUE, so the pH probe appears twice (pH and
    ORP). Adding up every row would count its current twice.
    """
    per_unit = {spec["unit"]: spec["effekt_w"] for spec in SENSORS}
    return sum(w[1] for w in per_unit.values() if w is not None)


def decode_register(raw, scale, signed):
    """Turn one 16-bit register into a number.

    Modbus has no notion of sign: a register is 16 bits and it is the reader
    who has to know whether to treat them as two's complement. Get it wrong
    on a signed register and an ORP of -12.5 mV comes back as 6541.1 mV,
    which is not an obviously wrong number in a log full of other numbers.
    Hence this is done in one place for every sensor rather than per sensor.
    """
    if signed and raw >= 0x8000:
        raw -= 0x10000
    return round(raw / scale, 3)


# ---------------------------------------------------------------------------
# LOCAL BUFFER  (requirement K-03, K-04)
# ---------------------------------------------------------------------------
# Measurements are written to disk BEFORE any attempt is made to send them.
# Doing it the other way round loses data every time the link drops, which out
# at a pond is often. On the ESP32 this becomes an SD card; the logic is
# identical.

class Buffer:
    def __init__(self, path=DB_FILE):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc   TEXT NOT NULL,
                payload  TEXT NOT NULL,
                sent     INTEGER NOT NULL DEFAULT 0
            )""")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_unsent ON readings(sent, id)")
        self.conn.commit()

    def store(self, ts, payload):
        self.conn.execute(
            "INSERT INTO readings (ts_utc, payload, sent) VALUES (?, ?, 0)",
            (ts, json.dumps(payload, separators=(",", ":"))))
        self.conn.commit()

    def unsent(self, limit=500):
        cur = self.conn.execute(
            "SELECT id, payload FROM readings WHERE sent = 0 "
            "ORDER BY id LIMIT ?", (limit,))
        return cur.fetchall()

    def mark_sent(self, ids):
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        self.conn.execute(
            f"UPDATE readings SET sent = 1 WHERE id IN ({marks})", ids)
        self.conn.commit()

    def counts(self):
        cur = self.conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN sent=0 THEN 1 ELSE 0 END) FROM readings")
        total, pending = cur.fetchone()
        return total or 0, pending or 0


# ---------------------------------------------------------------------------
# SENSOR READING  (requirement EL-03)
# ---------------------------------------------------------------------------
# A sensor that does not answer must not take the other measurements down with
# it. Each sensor gets a bounded number of retries, then a fault code is logged
# and polling continues.

def read_raw(client, unit, addr, retries):
    """Read one 16-bit register with retries. Returns (raw value, error)."""
    for attempt in range(1, retries + 1):
        try:
            rr = client.read_holding_registers(addr, count=1, slave=unit)
            if rr.isError():
                raise IOError(str(rr))
            return rr.registers[0], None
        except Exception as exc:
            if attempt == retries:
                return None, exc
            time.sleep(0.2)
    return None, IOError("retries exhausted")


def read_sensor(client, spec, retries, log):
    raw, exc = read_raw(client, spec["unit"], spec["addr"], retries)
    if raw is None:
        log(f"    {spec['label']:<16} SVARAR INTE efter "
            f"{retries} forsok ({type(exc).__name__})")
        return None, "no_response"
    return decode_register(raw, spec["scale"], spec["signed"]), None


# ---------------------------------------------------------------------------
# HOW MANY READINGS, AND WHAT TO DO WITH THEM
# ---------------------------------------------------------------------------
# None of this was specified anywhere, so it is decided here rather than left
# to arise as a side effect of how the loop happens to be written. Three
# questions, three answers, all behind "sampling" in config.json.
#
# 1. AVERAGE SEVERAL READINGS?  Yes -- three.
#
#    The expensive part of a measurement is the warm-up: half a minute with
#    the rail powered. Two extra reads cost some twenty milliseconds of bus
#    time on a rail that is already on, so they are free in every sense the
#    energy budget cares about. What they buy is a defence against the single
#    bad sample: a collision on the bus, a frame that arrived half written, a
#    sensor read a fraction too early.
#
#    Three is the smallest number that allows a median. Two can tell you that
#    something is wrong but not which of the two to believe.
#
# 2. FILTER OUTLIERS, BY WHAT CRITERION?  By taking the MEDIAN, which needs
#    no criterion at all.
#
#    A mean of three lets one garbage sample drag the answer a third of the
#    way towards itself. The median ignores it completely as long as the
#    other two agree. Every threshold-based filter needs a number somebody
#    has to defend, and this project has enough undefended numbers already.
#    The median has none.
#
#    Nothing is discarded quietly, though. When the three disagree by more
#    than spread_warn_frac the row is flagged "brusig" -- which happens to be
#    the one fault type in faults.json that nothing else here detects.
#
# 3. A VALUE OUTSIDE THE SENSOR'S RANGE?  Stored, and flagged. Never
#    discarded, never replaced.
#
#    An impossible reading is evidence: a broken sensor, a wrong scale
#    factor, or a signed register read as unsigned -- the fault that turns
#    -12.5 mV into 6541.1. Discarding it destroys the evidence and leaves a
#    gap that looks like a communication problem, which sends the next person
#    looking in the wrong place entirely. Substituting the previous value
#    would be worse, and is exactly what EL-03 forbids.
#
#    Only values with a stated range can be checked. In the pond profile all
#    three have one (pH 0-14, ORP +-2000 mV, turbidity 0-1000 NTU). The self
#    test lists any unchecked ones rather than letting anyone assume
#    otherwise.

def sample_sensor(client, spec, cfg, log):
    """Read one sensor several times over. Returns (value, fault or None).

    The value is the median of the readings. A fault describes what was odd
    about them -- it never changes the value itself.
    """
    import statistics

    sampling = cfg.get("sampling", {})
    antal = max(1, sampling.get("reads_per_measurement", 3))

    varden = []
    for _ in range(antal):
        value, err = read_sensor(client, spec, cfg["retries"], log)
        if err:
            # A sensor that did not answer the first time will not answer the
            # next two either, and each attempt costs a full round of retries
            # with the rail powered. Stop asking.
            return None, err
        varden.append(value)

    if sampling.get("method") == "mean":
        # Available for comparison, not recommended: see point 2 above.
        varde = round(statistics.mean(varden), 3)
    else:
        varde = round(statistics.median(varden), 3)
    fel = None

    # Did the readings disagree with each other? "noisy" is a real failure --
    # a loose terminal, water in a connector, a board on its way out -- and
    # until now nothing in the poller looked for it.
    #
    # Two conditions, not one. A purely relative threshold cries wolf at small
    # values: turbidity is quantised to tenths of an NTU, so in clear water
    # at 0.4 NTU a single step of the last digit is 25 per cent and nothing
    # is wrong at all. The spread therefore also has to exceed two steps of
    # the register's own resolution, which the profile knows because it
    # knows the scale.
    spann = max(varden) - min(varden)
    grans = sampling.get("spread_warn_frac", 0.10)
    minsta_spann = 2.0 * spec["resolution"]
    if (len(varden) > 1 and varde and abs(spann / varde) > grans
            and spann > minsta_spann):
        fel = "brusig"
        log(f"    {spec['label']:<16} {varde}  <- {antal} avlasningar spred "
            f"sig {spann:.3g} ({100 * abs(spann / varde):.0f} procent): "
            f"{varden}")

    # Outside what the datasheet says the sensor can measure at all?
    span = spec["range"]
    if sampling.get("range_check", True) and span is not None:
        if not (span[0] <= varde <= span[1]):
            fel = "out_of_range"
            log(f"    {spec['label']:<16} {varde}  <- UTANFOR MATOMRADET "
                f"{span[0]:g}-{span[1]:g}. Vardet sparas anda, med flagga.")
    return varde, fel


def read_thermometers(client, retries):
    """Read every thermometer. Returns ({unit: degrees}, faults)."""
    readings, faults = {}, {}
    for spec in TEMP_SENSORS:
        raw, exc = read_raw(client, spec["unit"], spec["addr"], retries)
        if raw is None:
            faults[f"temp_{spec['unit']}"] = "no_response"
            continue
        readings[spec["unit"]] = decode_register(
            raw, spec["scale"], spec["signed"])
    return readings, faults


# ---------------------------------------------------------------------------
# A VALUE THAT HAS STOPPED MOVING  (the "stuck" fault)
# ---------------------------------------------------------------------------
# The other half of the cross-checks in CLAUDE.md, next to temp_d: a sensor
# that keeps answering in time with a plausible number that happens to be
# wrong. temp_d needs a second thermometer to compare against; a frozen
# pH, ORP or turbidity reading has nothing to be compared against -- except
# its own past.
#
# So that is what this compares it to. A real pond never gives the same
# reading twice: pH and ORP swing over the day, and turbidity moves when the
# pond is pumped from or rained on. A value that is bit-identical for hours
# is a register that stopped being written, not water that stopped changing.
#
# It is a SUSPICION, not a verdict. A coarsely quantised value can honestly
# sit still for a while, so the flag says "misstankt" and a human decides.
# False suspicion costs a look at a graph; a missed frozen sensor costs a
# season of wrong data.
#
# The count lives in RAM. On the laptop that is fine. ON THE ESP32 IT IS NOT:
# deep sleep restarts the program every cycle, so a count in RAM never gets
# past one. The firmware keeps it in RTC memory -- see firmware/README.md.
_stuck_history = {}          # key -> [value, number of cycles it has been that]


def stuck_limit(key, cfg):
    """How many identical readings this parameter is allowed, from the profile.

    Per sensor, because the number follows from the register's RESOLUTION and
    not from the clock. A value quantised to whole units can honestly sit on
    the same number for hours in still water; one quantised to hundredths
    cannot. One global threshold either cries wolf about the first or sleeps
    through the second.

    config.json's stuck_cycles is the fallback, used for anything the profile
    does not name -- temp, for instance, which is not a sensor of its own.
    """
    for spec in SENSORS:
        if spec["key"] == key and spec.get("stuck_cycles"):
            return spec["stuck_cycles"]
    return cfg.get("stuck_cycles", 24)


def check_stuck(values, cfg, log):
    """Flag values that have not moved at all for their own stuck limit."""
    granskade = [spec["key"] for spec in SENSORS] + ["temp"]
    # batt is deliberately left out: it is hardcoded to 13.1 until there is a
    # real ADC, so it would be flagged every single cycle and teach whoever
    # reads the log to ignore the flag. temp_d is left out too --
    # it is a comparison, not a measurement.
    flags = {}

    for key in granskade:
        granser = stuck_limit(key, cfg)
        timmar = granser * cfg["measure_interval_s"] / 3600.0
        value = values.get(key)
        if value is None:
            # A silent sensor is a different fault with its own entry, and it
            # must not count towards this one.
            _stuck_history.pop(key, None)
            continue

        forra = _stuck_history.get(key)
        if forra is not None and forra[0] == value:
            forra[1] += 1
        else:
            _stuck_history[key] = [value, 1]

        antal = _stuck_history[key][1]
        if antal >= granser:
            flags[key] = "stuck"
            log(f"    [misstanke] {key} {value} har inte rort sig pa "
                f"{antal} matningar (ca {timmar:.1f} h)")
    return flags


def compare_thermometers(readings, log):
    """Turn the thermometer readings into one temperature and one spread.

    Returns (temp, spread). temp comes from the profile's temp_primar; spread is
    the largest gap between any two that answered, or None when fewer than two
    did -- one reading cannot disagree with itself.
    """
    if not readings:
        log(f"    {'temperatur':<16} INGEN av "
            f"{len(TEMP_SENSORS)} givare svarade")
        return None, None

    temp = readings.get(TEMP_PRIMARY_UNIT)
    if temp is None:
        # The usual source is silent, so fall back to any other thermometer
        # rather than dropping temperature from the measurement entirely.
        unit, temp = sorted(readings.items())[0]
        log(f"    {'temperatur':<16} ordinarie termometer tyst, "
            f"anvander enhet {unit}")

    spread = None
    if len(readings) >= 2:
        spread = round(max(readings.values()) - min(readings.values()), 2)
    return temp, spread


# ---------------------------------------------------------------------------
# WHICH BUS  --  the one difference between the laptop and the buoy
# ---------------------------------------------------------------------------
# The simulator answers over TCP because a laptop has no RS485 port. The buoy
# talks RTU over a converter on a serial line. That is the ONLY difference:
# the register map, the scales, the retries, the buffering and the alarm
# logic are byte for byte the same on both.
#
# The two halves of the serial setup come from two different places on
# purpose. Baud rate, parity, bits and stop bits are properties of the SENSOR
# FAMILY and live in the profile, next to the registers -- swap family and
# they follow. The port is a property of THIS MACHINE and lives in
# config.json. Neither is written down twice.


def bus_description(cfg):
    """One line saying which bus this run will use, for the start-up print."""
    m = cfg.get("modbus", {})
    if m.get("transport", "tcp") == "rtu":
        s = sensor_profiles.serial_settings(PROFILE)
        return (f"RTU {m.get('serial_port', '/dev/ttyUSB0')}  "
                f"{s['baudrate']} baud {s['bytesize']}{s['parity']}"
                f"{s['stopbits']}  (ur profilen {PROFILE['id']})")
    return f"TCP {m.get('host', '127.0.0.1')}:{m.get('port', 5020)}  (simulator)"


def open_client(cfg):
    """Build the Modbus client this configuration asks for. Not connected yet.

    Raises SystemExit on an unknown transport rather than quietly falling back
    to TCP: a buoy that thinks it is talking to real sensors and is in fact
    talking to nothing should say so on the first line, not after a night of
    empty measurements.
    """
    m = cfg.get("modbus", {})
    transport = m.get("transport", "tcp")
    timeout = m.get("timeout_s", 1.0)

    if transport == "tcp":
        from pymodbus.client import ModbusTcpClient
        return ModbusTcpClient(m.get("host", "127.0.0.1"),
                               port=m.get("port", 5020), timeout=timeout)

    if transport == "rtu":
        from pymodbus.client import ModbusSerialClient
        s = sensor_profiles.serial_settings(PROFILE)
        try:
            return ModbusSerialClient(port=m.get("serial_port", "/dev/ttyUSB0"),
                                      baudrate=s["baudrate"],
                                      bytesize=s["bytesize"],
                                      parity=s["parity"],
                                      stopbits=s["stopbits"],
                                      timeout=timeout)
        except RuntimeError as exc:
            # pymodbus only needs pyserial for the serial transport, and says
            # so in English with a pip command in it. Whoever is standing at
            # the bench with a converter in one hand deserves a straighter
            # answer than a traceback.
            raise SystemExit(
                f"Kan inte oppna serieporten: {exc}\n"
                f"Kor:  pip install -r requirements.txt")

    raise SystemExit(
        f"Okand transport '{transport}' i config.json.\n"
        f"Valjbara: \"tcp\" (simulatorn) eller \"rtu\" (riktiga givare).")


def measure_cycle(cfg, log):
    """One full measurement. Mirrors what the firmware does:

        power on  ->  wait out the warm-up  ->  read  ->  power off

    On the laptop, opening the Modbus connection IS the rail being powered
    and closing it is the rail being cut -- that is what makes the
    simulator's warm-up honest. Cutting it happens in a finally, so a sensor
    that throws halfway through does not leave the rail energised: on the
    buoy that is not an untidy log line, it is a flat battery.

    OVER RTU THAT EQUIVALENCE STOPS BEING TRUE. Opening a serial port powers
    nothing. The two log lines below are where the MOSFET has to be switched
    for real, and until that hook exists, "rtu" reads sensors that somebody
    else has already given power to.
    """
    client = open_client(cfg)
    values, temps, faults = {}, {}, {}
    started = time.time()

    log("  [ström på] sensorrail")
    if not client.connect():
        log("  KAN INTE ANSLUTA till sensorbussen")
        return None

    try:
        log(f"  [väntar]   {cfg['warmup_s']:g} s uppvärmning")
        time.sleep(cfg["warmup_s"])

        for spec in SENSORS:
            value, err = sample_sensor(client, spec, cfg, log)
            if err:
                faults[spec["key"]] = err
            # A flagged value is still a value. Only a sensor that said
            # nothing at all has none, and that is the one case where the
            # field is left out of the payload entirely.
            if value is not None:
                values[spec["key"]] = value
                if err is None:
                    log(f"    {spec['label']:<16} {value}")

        temps, temp_faults = read_thermometers(client, cfg["retries"])
        faults.update(temp_faults)
    finally:
        client.close()
        on_time = time.time() - started
        watt = sensor_power_w()
        log(f"  [ström av] sensorrail  {on_time:.1f} s x {watt:.2f} W = "
            f"{on_time * watt:.0f} Ws (databladens ovre granser)")

    temp, spread = compare_thermometers(temps, log)
    if temp is not None:
        values["temp"] = temp
        log(f"    {'temperatur':<16} {temp}")
    if spread is not None:
        values["temp_d"] = spread
        # Beyond what the two datasheets' accuracy figures allow together.
        oense = spread > sensor_profiles.TEMP_D_TOLERANCE_C
        note = "  <- givarna ar oense" if oense else ""
        log(f"    {'  spridning':<16} {spread}{note}")

    # Has anything stopped moving? Done after temp is in, so the temperature
    # is watched too -- a frozen thermometer that happens to be the one we
    # publish would otherwise slip past both this and temp_d.
    #
    # An existing fault is not overwritten: "out_of_range" says more about a
    # sensor than "stuck" does, and a value that is both is more interesting
    # for the first reason.
    for key, flagga in check_stuck(values, cfg, log).items():
        faults.setdefault(key, flagga)

    if not values:
        log("  INGEN sensor svarade. Ingen mätning sparas.")
        return None

    payload = {
        "boj": cfg["buoy_id"],
        "namn": cfg["buoy_name"],
        "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batt": read_battery(cfg),          # one ADC reading on the buoy
        **values,
    }
    if faults:
        payload["fel"] = faults
    return payload


# ---------------------------------------------------------------------------
# SENDING
# ---------------------------------------------------------------------------
# K-02 -- ALL TRANSMISSION TO THE SERVER SHALL BE ENCRYPTED
#
# It is not, in the development setup, and that is a deliberate choice
# documented at the end of README: on a laptop everything runs over
# localhost, and a broker with no password is one less thing to get wrong
# while the actual measurement chain is being built.
#
# The code path exists so the switch is a config change and not a rewrite:
#
#     "mqtt": {
#         "host": "din-server", "port": 8883,
#         "topic": "limna/LIMNA-001/measurements",
#         "tls": {"enabled": true, "ca_cert": "/etc/limna/ca.crt"},
#         "username_env": "LIMNA_MQTT_USER",
#         "password_env": "LIMNA_MQTT_PASS"
#     }
#
# CREDENTIALS COME FROM THE ENVIRONMENT, NOT FROM config.json. The file is
# gitignored today, but "gitignored" and "safe to put a password in" are not
# the same thing -- it gets copied, mailed and pasted into issues. Naming the
# variable in config and keeping the value in the environment means the
# secret never lives in a file that can be shared by accident.
#
# What the server side still needs for K-02 is written up in README under
# "Detta ar inte produktionsklart". None of it is done.


def mqtt_credentials(q):
    """Username and password from the environment, or (None, None).

    A password written directly in config.json is still honoured, because
    refusing it outright would only push somebody to disable TLS instead --
    but it is warned about, once, where whoever runs the buoy will see it.
    """
    user = os.environ.get(q.get("username_env", "LIMNA_MQTT_USER"))
    password = os.environ.get(q.get("password_env", "LIMNA_MQTT_PASS"))
    return user or q.get("username"), password or q.get("password")


def flush(buffer, cfg, log):
    """Send everything not yet sent. Called after every measurement.

    NOTHING in here is allowed to raise. Losing the link halfway through a
    batch is not an edge case out at a pond, it is the normal case --
    and it is at its worst right after an outage, when the queue is long and
    the send takes longest. A crash there costs far more than the batch: the
    local buffer only protects data that the measurement loop is still alive
    to write into it.
    """
    pending = buffer.unsent()
    if not pending:
        return 0

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log("  paho-mqtt saknas, hoppar over sandning")
        return 0

    q = cfg["mqtt"]
    try:
        # paho-mqtt 2.x requires the callback API version to be stated.
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        user, password = mqtt_credentials(q)
        if user:
            cli.username_pw_set(user, password)

        tls = q.get("tls", {})
        if tls.get("enabled", False):
            # ca_cert None makes paho use the system trust store, which is
            # what a certificate from a public authority needs. A private CA
            # -- the likely case for our own server -- goes in the file.
            cli.tls_set(ca_certs=tls.get("ca_cert"))
            if tls.get("insecure", False):
                # Skips the hostname check. Only for a first connection test
                # against an IP address; it removes most of the point of TLS.
                cli.tls_insecure_set(True)
                log("  [K-02]     TLS UTAN vardnamnskontroll -- "
                    "endast for test")

        cli.connect(q["host"], q["port"], keepalive=30)
        cli.loop_start()
    except Exception as exc:
        log(f"  [sandning] ingen kontakt med brokern ({type(exc).__name__}). "
            f"{len(pending)} matningar ligger kvar i bufferten.")
        return 0

    # A row is marked sent only once the broker has acknowledged it. If the
    # link dies halfway through, the acknowledged rows are marked and the
    # rest stay queued for the next attempt -- never the other way round, and
    # never the whole batch on the strength of a partial send.
    sent_ids = []
    stopped_by = None
    for row_id, payload in pending:
        try:
            info = cli.publish(q["topic"], payload, qos=1)
            info.wait_for_publish(timeout=5)
            confirmed = info.is_published()
        except Exception as exc:
            # publish(), wait_for_publish() and is_published() all raise
            # RuntimeError once the connection is gone. That is the line
            # that used to take the whole process down with it.
            stopped_by = exc
            break
        if not confirmed:
            # The socket is still open but nothing is coming back -- a stalled
            # link rather than a closed one. Same handling, different cause.
            stopped_by = TimeoutError("ingen bekraftelse inom 5 s")
            break
        sent_ids.append(row_id)

    try:
        cli.loop_stop()
        cli.disconnect()
    except Exception:
        pass                    # the link is already gone; nothing to close

    buffer.mark_sent(sent_ids)

    if stopped_by is not None:
        kvar = len(pending) - len(sent_ids)
        log(f"  [sandning] AVBRUTEN efter {len(sent_ids)} av {len(pending)} "
            f"matningar ({type(stopped_by).__name__})")
        log(f"             {kvar} ligger kvar i bufferten "
            f"och skickas nasta gang.")
        return len(sent_ids)

    if len(sent_ids) > 1:
        log(f"  [sandning] {len(sent_ids)} matningar skickade "
            f"(varav {len(sent_ids) - 1} eftersläpande)")
    elif sent_ids:
        log("  [sandning] 1 matning skickad")
    return len(sent_ids)


# ---------------------------------------------------------------------------
# WHEN TO SEND  (requirements F-12, and the radio half of the energy budget)
# ---------------------------------------------------------------------------
# Measuring and sending are separate on purpose. The radio -- WiFi in the
# prototype, LoRa or a cellular modem later -- is the second largest load
# after the sensor rail, and for WiFi and cellular most of what it costs is
# waking up and joining the network, not moving the bytes. Sending each
# measurement on its own therefore costs several times what sending a batch
# does, for exactly the same data.
#
# So measurements accumulate in the buffer and go out in batches. The buffer
# was already doing this during outages; this just makes it the normal case.

def bradskande(payload, cfg):
    """Which values in this measurement are outside their limits (F-12).

    Requirement F-12: an alarm goes out immediately whatever the ordinary
    interval says. A limit crossed at 02:00 is worth very little if it
    arrives at 08:00, and that difference is most of what separates a
    product from a logger.

    The limits come from config.json. Grafana keeps its own copies as the
    second line of defence -- see the comment on alarm_limits above for why
    that duplication is on purpose.

    A value that is missing is NOT urgent here, and that is a decision rather
    than an omission. A sensor that has gone quiet leaves no number to
    compare, and inventing one would be the very thing EL-03 forbids. It also
    calls for a service visit rather than immediate action, and the silence
    rules on the server catch it at the next send anyway -- waking the radio
    for it costs more than it gives. The fault is recorded in the payload's
    fel field either way.

    Returns a list of reasons in Swedish, empty when nothing is wrong, so
    the log can say WHICH limit went and not merely that one did.
    """
    limits = cfg.get("alarm_limits", {})
    skal = []
    for key in sorted(limits):
        if key.startswith("_"):          # _note and friends are not limits
            continue
        value = payload.get(key)
        if value is None:
            continue
        low = limits[key].get("low")
        high = limits[key].get("high")
        if low is not None and value < low:
            skal.append(f"{key} {value} under gransen {low}")
        if high is not None and value > high:
            skal.append(f"{key} {value} over gransen {high}")
    return skal


BESLUTADE_GRANSER = {"batt"}    # the rest are set with the farmer


def limit_summary(cfg):
    """One short line per limit, for the start-up print.

    Limits that are still placeholders are marked, because a number printed
    without provenance is a number somebody will trust.
    """
    limits = cfg.get("alarm_limits", {})
    delar = []
    for key in sorted(limits):
        if key.startswith("_"):
            continue
        if key not in BESLUTADE_GRANSER:
            key_visas = key + "*"
        else:
            key_visas = key
        low, high = limits[key].get("low"), limits[key].get("high")
        if low is not None and high is not None:
            delar.append(f"{key_visas} {low}-{high}")
        elif low is not None:
            delar.append(f"{key_visas} <{low}")
        elif high is not None:
            delar.append(f"{key_visas} >{high}")
    if not delar:
        return "inga satta"
    return "  ".join(delar) + "   (* = PLACEHOLDER, satts med bonden)"


# ---------------------------------------------------------------------------
# BATTERY AND ECO MODE  (requirements E-07, F-02)
# ---------------------------------------------------------------------------
# batt was hardcoded to 13.1 until 2026-08-10, which had two consequences: the
# low battery alarm could never be provoked, and eco mode had nothing to
# switch on. E-07 makes the battery an ordinary measured parameter, so it is
# read here like any other -- except that on the buoy it is one ADC reading on
# the microcontroller, not a Modbus sensor. That is the whole of what changes
# in the port:
#
#     value = adc_read(BATT_PIN) * VOLTAGE_DIVIDER
#
# There is no ADC on a laptop, so the source is configurable. "fixed" keeps
# the old behaviour. "sim" drains a starting voltage at a set rate so eco
# mode, the low battery alarm and the F-12 immediate send can all be
# provoked without waiting for a real battery to go flat. It is a test aid
# and models nothing about a real LiFePO4 battery.
_battery_started_at = None


def read_battery(cfg):
    """Battery voltage. One ADC reading on the buoy, a stand-in here."""
    global _battery_started_at
    batt = cfg.get("battery", {"source": "fixed", "volt": 13.1})
    volt = batt.get("volt", 13.1)

    if batt.get("source") != "sim":
        return volt

    if _battery_started_at is None:
        _battery_started_at = time.time()
    hours = (time.time() - _battery_started_at) / 3600.0
    drained = volt - batt.get("drain_v_per_h", 0.0) * hours
    # A real battery does not fall below its own floor either; clamping keeps
    # the simulation from reporting nonsense after a long run.
    return round(max(batt.get("floor_v", 10.8), drained), 2)


def eco_state(cfg, batt, in_eco, log):
    """F-02: should the buoy be in energy saving mode?

    Two thresholds, not one. A single limit would have the buoy flipping in
    and out of eco mode every cycle while the voltage sits on it, and every
    flip changes the measurement interval -- exactly the kind of jitter that
    makes a series hard to read afterwards.

    And both of them sit WELL ABOVE the low battery alarm. Saving power at
    the same voltage that raises the alarm is saving power too late; see the
    note on eco_mode in DEFAULT_CONFIG.
    """
    eco = cfg.get("eco_mode", {})
    if not eco.get("enabled", True) or batt is None:
        return in_eco

    if not in_eco and batt < eco.get("enter_v", 13.0):
        log(f"  [F-02]     batteriet {batt} V under {eco.get('enter_v', 13.0)}"
            f" -- gar i ENERGISPARLAGE")
        return True
    if in_eco and batt > eco.get("exit_v", 13.2):
        log(f"  [F-02]     batteriet {batt} V over {eco.get('exit_v', 13.2)}"
            f" -- ater till normalt lage")
        return False
    return in_eco


def current_intervals(cfg, in_eco):
    """(measure interval, send interval) for the mode the buoy is in."""
    if in_eco:
        eco = cfg.get("eco_mode", {})
        return (eco.get("measure_interval_s", 3600),
                cfg.get("eco_send_interval_s", 21600))
    return cfg["measure_interval_s"], cfg["send_interval_s"]


def load_config():
    """Read config.json, creating or topping it up as needed.

    A config file written before a setting existed is missing that setting,
    and the buoy would then run on a default nobody can see in the file. So
    anything absent is filled in from DEFAULT_CONFIG and written back, and
    the poller says which keys it added. Existing values are never touched.
    """
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as fh:
            json.dump(DEFAULT_CONFIG, fh, indent=2)
        print(f"Skapade {os.path.basename(CONFIG_FILE)} med standardvarden.")

    with open(CONFIG_FILE) as fh:
        cfg = json.load(fh)

    # One level down as well, because most settings live inside a block. A
    # config.json written before "transport" existed has a modbus block
    # without it, and topping up only the top level would leave the buoy
    # running on a default that is nowhere to be seen in the file -- which is
    # the exact problem this function exists to prevent.
    saknas = []
    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = value
            saknas.append(key)
        elif isinstance(value, dict) and isinstance(cfg[key], dict):
            for underkey, undervalue in value.items():
                if underkey.startswith("_"):
                    continue        # _note and _krav are prose, not settings
                if underkey not in cfg[key]:
                    cfg[key][underkey] = undervalue
                    saknas.append(f"{key}.{underkey}")

    if saknas:
        with open(CONFIG_FILE, "w") as fh:
            json.dump(cfg, fh, indent=2)
        print(f"Kompletterade {os.path.basename(CONFIG_FILE)} med "
              f"{', '.join(sorted(saknas))}.")
    return cfg


def main():
    cfg = load_config()
    buffer = Buffer()

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    total, pending = buffer.counts()
    print("LIMNA pollare")
    print(f"  Boj              {cfg['buoy_id']}  {cfg['buoy_name']}")
    print(f"  Sensorprofil     {PROFILE['id']}  "
          f"({len(sensor_profiles.sensors(PROFILE))} givare, {len(SENSORS)} matvarden, "
          f"{len(TEMP_SENSORS)} termometrar)")
    print(f"  Buss             {bus_description(cfg)}")
    print(f"  Matintervall     {cfg['measure_interval_s']} s")
    print(f"  Sandintervall    {cfg['send_interval_s']} s")
    print(f"  Uppvarmning      {cfg['warmup_s']} s")
    eco = cfg.get("eco_mode", {})
    if eco.get("enabled", True):
        print(f"  Energisparlage   under {eco.get('enter_v', 12.4)} V: "
              f"mater var {eco.get('measure_interval_s', 3600)} s, sander var "
              f"{cfg.get('eco_send_interval_s', 21600)} s "
              f"(ater over {eco.get('exit_v', 12.8)} V)")
    else:
        print("  Energisparlage   avstangt")
    batt = cfg.get("battery", {})
    print(f"  Batteri          {read_battery(cfg)} V, kalla "
          f"{batt.get('source', 'fixed')}")
    print(f"  Givareffekt      {sensor_power_w():.2f} W med railen pa "
          f"(databladens ovre granser)")
    print(f"  Buffert          {total} matningar, {pending} oskickade")
    print(f"  Larmgranser      {limit_summary(cfg)}")

    # The trap documented in etapper.md, now with teeth: with the warm-up cut
    # short the sensors read low, and since 2026-08-10 the buoy acts on that
    # itself instead of only drawing it in a graph. A development shortcut
    # would send an "alarm" every cycle.
    langsammast = max(spec["warmup_s"] for spec in SENSORS)
    if cfg["warmup_s"] < langsammast:
        print(f"\n  VARNING: warmup_s ar {cfg['warmup_s']} s men langsammaste "
              f"givaren behover {langsammast:g} s.")
        print("  Varden fran en for kort uppvarmning lases for lagt, och "
              "larmgranserna")
        print("  utvarderas mot dem. Rakna med falsklarm som kommer fran "
              "matmetoden.")

    print("  Avsluta med Ctrl+C\n")

    # EL-05 -- automatic restart after a program fault.
    #
    # The buoy sits anchored in a pond. Nobody is there to notice a traceback
    # and start the program again, so an unhandled exception is not a crash,
    # it is a site visit. Every cycle therefore runs inside its own net: log
    # what happened, sleep the normal interval, try again next time round.
    #
    # KeyboardInterrupt is deliberately NOT caught by this net. It does not
    # inherit from Exception, so Ctrl+C still stops the program as usual.
    consecutive_faults = 0
    # Zero means "never sent", so the first measurement goes out at once
    # instead of sitting in the buffer for an hour while you watch an empty
    # dashboard and wonder what is wrong.
    last_send = 0.0
    in_eco = False              # F-02, decided from the battery every cycle
    try:
        while True:
            started = time.time()
            matintervall, fonster = current_intervals(cfg, in_eco)
            try:
                log("MATNING")
                payload = measure_cycle(cfg, log)
                if payload:
                    # F-02. Decided AFTER the measurement, so the reading that
                    # triggered the change is the one that goes out with it.
                    in_eco = eco_state(cfg, payload.get("batt"), in_eco, log)
                    matintervall, fonster = current_intervals(cfg, in_eco)
                    # To disk FIRST, always -- K-03. An urgent measurement is
                    # still written before anything is sent, so a limit
                    # crossing survives a link that will not come up.
                    buffer.store(payload["t"], payload)

                    # The window is measured from the last SUCCESSFUL send, so
                    # a failed attempt does not push it an hour into the
                    # future and strand the data that is already queued.
                    sedan = time.time() - last_send
                    skal = bradskande(payload, cfg)
                    if skal:
                        for rad in skal:
                            log(f"  [LARM]     {rad}")
                        log("  [sandning] BRADSKANDE -- skickas direkt (F-12)")
                        skicka = True
                    else:
                        skicka = sedan >= fonster

                    if skicka:
                        if flush(buffer, cfg, log):
                            last_send = time.time()
                    else:
                        _, vantar = buffer.counts()
                        log(f"  [sandning] hoppas over, {fonster - sedan:.0f} s "
                            f"kvar till nasta fonster. "
                            f"{vantar} matningar vantar.")

                _, pending = buffer.counts()
                if pending:
                    log(f"  {pending} matningar vantar i bufferten")
                consecutive_faults = 0
            except Exception as exc:
                consecutive_faults += 1
                log(f"  OVANTAT FEL   {type(exc).__name__}: {exc}")
                log(f"  Cykeln overhoppad ({consecutive_faults} i rad). "
                    f"Forsoker igen nasta varv.")
                # The full traceback only on the first fault of a run. A
                # permanent fault would otherwise fill the log with the same
                # twenty lines every quarter of an hour for weeks.
                if consecutive_faults == 1:
                    for line in traceback.format_exc().rstrip().splitlines():
                        log(f"    | {line}")

            elapsed = time.time() - started
            wait = max(0, matintervall - elapsed)
            lage = "  (energisparlage, F-02)" if in_eco else ""
            log(f"  [sover]    {wait:.0f} s{lage}\n")
            time.sleep(wait)
    except KeyboardInterrupt:
        total, pending = buffer.counts()
        print(f"\nAvslutad. {total} matningar i bufferten, {pending} oskickade.")


# ---------------------------------------------------------------------------
# SELF TEST -- exercises the buffer without pymodbus or a broker
# ---------------------------------------------------------------------------

def selftest():
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    buf = Buffer(path)

    print(f"Sensorprofil: {PROFILE['id']}")
    print(f"Buss:         {bus_description(DEFAULT_CONFIG)}\n")
    print(f"  {'adr':>3} {'falt':<5} {'register':>8} {'skala':>7} {'typ':<10} "
          f"uppvarmning")
    for spec in SENSORS:
        typ = "signerad" if spec["signed"] else "osignerad"
        print(f"  {spec['unit']:>3} {spec['key']:<5} 0x{spec['addr']:04X} "
              f"{spec['scale']:>9} {typ:<10} {spec['warmup_s']:g} s")

    # Does the simulator still read the same map as we do? Both build theirs
    # from sensor_profiles.py, so this only fails if somebody has written a
    # register number back into one of the two files by hand -- which is the
    # fault that used to be invisible. Guarded because the simulator is not
    # installed on the buoy; the poller must run without it.
    print("\n  Kartkontroll mot simulatorn")
    try:
        import fake_sensors            # noqa: F401  (imported for the check)
    except ImportError:
        print("    fake_sensors.py saknas, hoppar over (normalt pa hardvara)")
    else:
        sensor_profiles.verify_consistency(log=lambda m: print("  " + m))

    print("\nBuffertlogik\n")
    for i in range(5):
        buf.store(f"2026-07-27T10:0{i}:00Z", {"ph": 7.6 + i * 0.1, "n": i})
    print(f"  5 matningar sparade        -> totalt {buf.counts()[0]}, "
          f"oskickade {buf.counts()[1]}")

    ids = [r[0] for r in buf.unsent()][:3]
    buf.mark_sent(ids)
    print(f"  3 markerade som skickade   -> totalt {buf.counts()[0]}, "
          f"oskickade {buf.counts()[1]}")

    print("\n  Simulerat tackningsbortfall: 4 matningar utan kontakt")
    for i in range(4):
        buf.store(f"2026-07-27T11:0{i}:00Z", {"ph": 7.8, "n": 100 + i})
    print(f"  -> oskickade {buf.counts()[1]}")

    print("  Kontakten aterkommer, allt skickas i efterhand")
    ids = [r[0] for r in buf.unsent()]
    buf.mark_sent(ids)
    print(f"  -> oskickade {buf.counts()[1]}  "
          f"(krav K-03 uppfyllt: inget tappat)")

    print("\n  Nyttolast som skickas till brokern:")
    # Built the same way measure_cycle() builds it, so the byte count below is
    # the real one for the pond profile: pH, ORP, turbidity, the published
    # temperature and the gap between the two thermometers.
    example = {
        "boj": DEFAULT_CONFIG["buoy_id"],
        "namn": DEFAULT_CONFIG["buoy_name"],
        "t": "2026-07-27T14:00:00+00:00",
        "batt": 13.1,
        "ph": 7.83, "orp": 239.5, "turb": 6.4, "temp": 18.3, "temp_d": 0.25,
    }

    js = json.dumps(example, separators=(",", ":"))
    print(f"    {js}")
    print(f"    {len(js)} byte")
    print(f"    Verkliga nyttolaster varierar nagra byte med hur manga")
    print(f"    decimaler varje matvarde hamnar pa.")
    per_month = len(js) * (24 * 60 / 15) * 30 / 1024
    print(f"    Vid 15 min intervall: {per_month:.0f} KiB data per manad.")

    # -- F-12: does the buoy recognise an alarm on its own? --------------
    print("\nF-12: bojen kanner igen ett larm utan servern\n")
    print(f"  Granser: {limit_summary(DEFAULT_CONFIG)}\n")
    fall = [
        ("normal matning", {}),
        ("hogt pH (algblomning)", {"ph": 9.4}),
        ("grumligt efter regn", {"turb": 72.0}),
        ("lagt batteri", {"batt": 12.2}),
        ("pH-givaren tyst", {"ph": None, "orp": None}),
    ]
    for vad, andring in fall:
        matning = dict(example)
        for key, value in andring.items():
            if value is None:
                matning.pop(key, None)      # sensor did not answer at all
            else:
                matning[key] = value
        skal = bradskande(matning, DEFAULT_CONFIG)
        if skal:
            print(f"  {vad:<24} BRADSKANDE  {skal[0]}")
        else:
            print(f"  {vad:<24} vantar pa nasta sandfonster")
    print("\n  En tyst givare ger inget varde att jamfora och blir darfor "
          "inte")
    print("  bradskande har. Det fallet fangas av fel-faltet och av "
          "tystnadsreglerna")
    print("  i Grafana, inte av en grans (krav EL-03).")

    # -- three readings, median, and what happens to an impossible one ----
    print("\nDatabehandling: tre avlasningar, median\n")
    import statistics
    fall = [
        ("normal spridning", [7.83, 7.84, 7.82]),
        ("ett skrapvarde", [7.83, 0.12, 7.84]),
        ("tva skrapvarden", [7.83, 0.12, 0.11]),
    ]
    print(f"  {'fall':<18} {'avlasningar':<22} {'median':>8} {'medel':>8}")
    for vad, varden in fall:
        print(f"  {vad:<18} {str(varden):<22} "
              f"{statistics.median(varden):>8.2f} "
              f"{statistics.mean(varden):>8.2f}")
    print("\n  Medianen haller stand mot ett skrapvarde. Mot tva gor den det")
    print("  inte, och kan inte -- da ar det inte langre en utliggare utan en")
    print("  trasig givare, och det ar vad flaggan \"brusig\" ar till for.")

    print("\n  Matomraden som gar att kontrollera\n")
    for spec in SENSORS:
        if spec["range"] is None:
            print(f"    {spec['label']:<16} inget angivet i databladet "
                  f"-- kan inte kontrolleras")
        else:
            print(f"    {spec['label']:<16} {spec['range'][0]:g}"
                  f"-{spec['range'][1]:g}")
    print("\n  Ett varde utanfor omradet SPARAS med flaggan out_of_range.")
    print("  Det kasseras inte och ersatts aldrig med ett tidigare varde:")
    print("  ett omojligt tal ar bevis for trasig givare, fel skalfaktor")
    print("  eller ett signerat register last som osignerat (krav EL-03).")

    # -- a value that has stopped moving ---------------------------------
    print("\nFastfruset varde: troskeln foljer registrets upplosning\n")
    prov = dict(DEFAULT_CONFIG)
    prov["measure_interval_s"] = 900
    for key in [spec["key"] for spec in SENSORS] + ["temp"]:
        n = stuck_limit(key, prov)
        varifran = "profilen" if any(
            spec["key"] == key and spec.get("stuck_cycles")
            for spec in SENSORS) else "config.json"
        print(f"  {key:<6} {n:>4} matningar  ({n * 900 / 3600:>4.0f} h)  "
              f"ur {varifran}")
    print("\n  Troskeln ligger per givare i profilen, eftersom den foljer")
    print("  registrets upplosning: ett grovt kvantiserat varde kan arligt")
    print("  sta still lange, ett fint kan det inte.")

    print("\n  Demonstration med temperaturen, troskel sankt till 5:\n")
    _stuck_history.clear()
    prov["stuck_cycles"] = 5              # temp takes its limit from config
    tyst = lambda *_: None
    print(f"  {'matning':>8} {'ph':>7} {'temp':>7}  flaggat")
    for i in range(1, 8):
        # Everything moves a little, the way a pond does. Only temp is frozen.
        matning = {"ph": round(7.60 + i * 0.03, 2),
                   "orp": round(250.0 + i * 1.5, 1),
                   "turb": round(6.0 + i * 0.3, 1),
                   "temp": 18.20}
        flaggor = check_stuck(matning, prov, tyst)
        print(f"  {i:>8} {matning['ph']:>7} {matning['temp']:>7}  "
              f"{', '.join(sorted(flaggor)) or '-'}")
    print("\n  Temperaturen flaggas fran femte matningen, pH aldrig -- "
          "det ror sig.")
    print("  Flaggan hamnar i fel-faltet: \"fel\":{\"temp\":\"stuck\"}.")
    _stuck_history.clear()

    # -- decoding, checked against the manuals' own example frames --------
    # These are the bytes Seeed printed in the manuals, with the values they
    # say the bytes mean. If the profile or decode_register() is wrong, this
    # is where it shows -- before a real sensor is anywhere near the bus.
    print("\nAvkodning mot manualernas egna exempelsvar\n")
    regs_ph = {0x0000: 0x09F1, 0x0001: 0x030F, 0x0002: 0x095B}
    regs_turb = {0x0100: 0x00C0, 0x0101: 0x03D9}
    facit = {"ph": 7.83, "orp": 239.5, "turb": 98.5}
    alla_ok = True
    for spec in SENSORS:
        regs = regs_ph if spec["unit"] == 1 else regs_turb
        varde = decode_register(regs[spec["addr"]], spec["scale"],
                                spec["signed"])
        ok = abs(varde - facit[spec["key"]]) < 1e-6
        alla_ok = alla_ok and ok
        print(f"  {spec['key']:<5} 0x{regs[spec['addr']]:04X} -> {varde:>7g}  "
              f"manualen sager {facit[spec['key']]:<6g} "
              f"{'OK' if ok else 'FEL'}")
    for spec, facit_t in zip(TEMP_SENSORS, (25.45, 19.2)):
        regs = regs_ph if spec["unit"] == 1 else regs_turb
        varde = decode_register(regs[spec["addr"]], spec["scale"],
                                spec["signed"])
        ok = abs(varde - facit_t) < 1e-6
        alla_ok = alla_ok and ok
        print(f"  {spec['label']:<16} 0x{regs[spec['addr']]:04X} -> "
              f"{varde:g} C  manualen sager {facit_t:g}  "
              f"{'OK' if ok else 'FEL'}")
    print(f"\n  {'Alla stammer.' if alla_ok else 'NAGOT STAMMER INTE.'}")

    print("\nSignerad avkodning (ORP kan bli negativt)\n")
    print(f"  {'raatt':>8} {'signerat':>10} {'osignerat':>11}  register")
    checks = [
        (0x095B, 10, "ORP 0x0002"),     # 239.5 mV, the manual's example
        (0x0000, 10, "ORP 0x0002"),     # 0 mV
        (0xFF83, 10, "ORP 0x0002"),     # -12.5 mV, water without oxygen
    ]
    for raw, scale, what in checks:
        print(f"  {raw:8d} {decode_register(raw, scale, True):10.1f} "
              f"{decode_register(raw, scale, False):11.1f}  {what}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
