"""
LIMNA firmware - turning register values into a measurement and a payload.

The same rules as laptop/poller.py, which is the reference:

    three reads per value, the median kept       (sample_sensor)
    "brusig" when the three disagree              (spread_warn_frac)
    "out_of_range" outside the datasheet range    -- flagged, never discarded
    a sensor that does not answer gets NO field   -- only a line in "fel"
    temp from the profile's temp_primar, temp_d the gap between thermometers

Plain Python: runs on the board and on the laptop (test_firmware.py).
"""


def decode(raw, scale, signed):
    """One 16-bit register to a number. Signed means two's complement."""
    if signed and raw >= 0x8000:
        raw -= 0x10000
    return round(raw / scale, 3)


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def sample(bus, spec, reads=3, retries=3, spread_frac=0.10):
    """Read one value `reads` times. Returns (value or None, fault or None)."""
    values = []
    for _ in range(reads):
        raw = None
        for _attempt in range(retries):
            try:
                raw = bus.read(spec["unit"], spec["addr"])[0]
                break
            except Exception:
                pass
        if raw is None:
            # It did not answer once; the next reads would only cost more
            # time with the rail on.
            return None, "no_response"
        values.append(decode(raw, spec["scale"], spec["signed"]))

    value = round(median(values), 3)
    fault = None
    spread = max(values) - min(values)
    if (value and abs(spread / value) > spread_frac
            and spread > 2 * spec["resolution"]):
        fault = "brusig"
    span = spec["range"]
    if span is not None and not (span[0] <= value <= span[1]):
        fault = "out_of_range"
    return value, fault


def thermometers(bus, temp_specs, primary_unit, retries=3):
    """Returns (temp, temp_d, faults). temp_d is None with under two readings."""
    readings, faults = {}, {}
    for spec in temp_specs:
        raw = None
        for _attempt in range(retries):
            try:
                raw = bus.read(spec["unit"], spec["addr"])[0]
                break
            except Exception:
                pass
        if raw is None:
            faults["temp_%d" % spec["unit"]] = "no_response"
            continue
        readings[spec["unit"]] = decode(raw, spec["scale"], spec["signed"])
    if not readings:
        return None, None, faults
    temp = readings.get(primary_unit)
    if temp is None:
        temp = readings[sorted(readings)[0]]     # fall back to any of them
    spread = None
    if len(readings) >= 2:
        spread = round(max(readings.values()) - min(readings.values()), 2)
    return temp, spread, faults


def build_payload(cfg, t, batt, values, temp, temp_d, faults):
    """The JSON line. Field names are fixed -- see CLAUDE.md before changing."""
    payload = {"boj": cfg["buoy_id"], "namn": cfg["buoy_name"], "t": t,
               "batt": batt}
    payload.update(values)
    if temp is not None:
        payload["temp"] = temp
    if temp_d is not None:
        payload["temp_d"] = temp_d
    if faults:
        payload["fel"] = faults
    return payload


def urgent(payload, limits):
    """F-12: reasons this measurement must go out now, empty if none."""
    reasons = []
    for key in sorted(limits):
        if key.startswith("_"):
            continue
        value = payload.get(key)
        if value is None:
            continue
        low, high = limits[key].get("low"), limits[key].get("high")
        if low is not None and value < low:
            reasons.append("%s %s under %s" % (key, value, low))
        if high is not None and value > high:
            reasons.append("%s %s over %s" % (key, value, high))
    return reasons


def iso_utc(tm):
    """time.gmtime() tuple -> the timestamp format telegraf expects."""
    return "%04d-%02d-%02dT%02d:%02d:%02d+00:00" % tm[:6]
