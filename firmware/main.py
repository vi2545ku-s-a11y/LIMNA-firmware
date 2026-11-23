"""
LIMNA firmware - the measurement cycle. MicroPython runs this file at power-on.

The order is the one in firmware/README.md, and it is a requirement, not a
style:

    rail on  ->  wait out the warm-up  ->  read the sensors  ->  rail off
    ->  timestamp  ->  write to the buffer  ->  send if it is time  ->  sleep

The measurement goes to the buffer BEFORE any attempt to send it (K-03). A
sensor that does not answer must not stop the others (EL-03). A fault in one
cycle must not stop the next (EL-05).

laptop/poller.py is the reference for every rule here.

NOT YET IN THIS VERSION (later steps in firmware/README.md):
    deep sleep and RTC memory   step 4 -- the board stays awake for now
    battery ADC, MOSFET, WDT    step 4 -- batt is a fixed number from config
    stuck-value check           needs RTC memory to survive deep sleep
    split rail, eco mode        follow once the hardware for them exists

Stop it from the laptop with Ctrl+C (mpremote does this when it connects).
"""

import json
import sys
import time

import sensor_profiles
from buffer import Buffer
import measure
import net

DEFAULTS = {
    "buoy_id": "LIMNA-ESP32",
    "buoy_name": "ESP32 testkort",
    "position": {"lat": 55.7047, "lon": 13.1910},
    "bus": "fake",                     # "fake" until the RS485 module is here
    "measure_interval_s": 60,
    "send_interval_s": 120,
    "warmup_s": 30,
    "retries": 3,
    "reads_per_measurement": 3,
    "battery_v": 13.1,                 # no ADC yet (step 4)
    "utc_offset_h": 1,                 # only the simulated pond's daylight
    "pins": {"uart": 1, "tx": 17, "rx": 18, "de": 16, "re": 15},
    # The same placeholders as laptop/poller.py. Set with the farmer.
    "alarm_limits": {"ph": {"low": 6.0, "high": 9.0},
                     "turb": {"low": None, "high": 50.0},
                     "batt": {"low": 12.4, "high": None}},
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open("config.json") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError) as exc:
        print("config.json saknas eller ar trasig:", exc)
    for key in ("wifi", "mqtt"):
        if key not in cfg:
            print("VARNING: '%s' saknas i config.json -- inget skickas" % key)
    return cfg


def make_bus(cfg, profile, specs, temps):
    if cfg["bus"] == "rtu":
        from modbus import RTUBus
        return RTUBus(sensor_profiles.serial_settings(profile), cfg["pins"])
    from fakebus import FakeBus
    return FakeBus(profile, specs, temps, utc_offset_h=cfg["utc_offset_h"])


def measure_cycle(cfg, bus, specs, temps, primary):
    """One measurement: power, warm-up, read, cut. Returns the payload or None."""
    values, faults = {}, {}
    bus.power_on()
    try:
        # Never shorter than the slowest sensor: a short warm-up gives values
        # that look plausible and are wrong -- which is what the simulation
        # is there to show.
        warm = cfg["warmup_s"]
        print("  [vantar]   %g s uppvarmning" % warm)
        time.sleep(warm)
        for spec in specs:
            value, fault = measure.sample(bus, spec, cfg["reads_per_measurement"],
                                          cfg["retries"])
            if fault:
                faults[spec["key"]] = fault
            if value is not None:
                values[spec["key"]] = value
            print("    %-12s %s%s" % (spec["label"], value,
                                      "  <- " + fault if fault else ""))
        temp, temp_d, t_faults = measure.thermometers(bus, temps, primary,
                                                      cfg["retries"])
        faults.update(t_faults)
        print("    %-12s %s   spridning %s" % ("temperatur", temp, temp_d))
    finally:
        bus.power_off()                 # EL-05: never leave the rail on

    if not values and temp is None:
        print("  INGEN givare svarade. Ingen matning sparas.")
        return None
    t = measure.iso_utc(time.gmtime())
    return measure.build_payload(cfg, t, cfg["battery_v"], values, temp,
                                 temp_d, faults)


def flush(buf, cfg):
    """Send what is waiting. Only confirmed lines are marked as sent."""
    entries = buf.unsent()
    if not entries:
        return 0
    if not net.wifi(cfg):
        return 0
    confirmed = net.send([line for _, _, line in entries], cfg)
    buf.mark_sent(entries[:confirmed])
    print("  [sandning] %d av %d skickade, %d vantar" %
          (confirmed, len(entries), buf.pending()))
    return confirmed


def main():
    cfg = load_config()
    sensor_profiles.use_config("config.json")
    profile = sensor_profiles.load_profile()
    specs = sensor_profiles.value_specs(profile)
    temps = sensor_profiles.temp_specs(profile)
    primary = sensor_profiles.temp_primary_unit(profile)
    bus = make_bus(cfg, profile, specs, temps)
    buf = Buffer()

    print("LIMNA firmware")
    print("  Boj           %s  %s" % (cfg["buoy_id"], cfg["buoy_name"]))
    print("  Profil        %s, %d matvarden" % (profile["id"], len(specs)))
    print("  Buss          %s" % cfg["bus"])
    print("  Intervall     matning %d s, sandning %d s" %
          (cfg["measure_interval_s"], cfg["send_interval_s"]))
    print("  Buffert       %d vantar" % buf.pending())

    net.wifi(cfg)
    last_send = 0
    while True:
        started = time.time()
        try:
            # The timestamp is set here, on the buoy, so it must be right.
            # Without a clock there is no honest timestamp to give.
            if not net.clock_valid():
                if not (net.wifi(cfg) and net.sync_clock()):
                    print("  Ingen giltig klocka -- vantar med att mata.")
                    time.sleep(10)
                    continue
            print("MATNING", measure.iso_utc(time.gmtime()))
            payload = measure_cycle(cfg, bus, specs, temps, primary)
            if payload:
                buf.store(payload)              # to disk FIRST (K-03)
                reasons = measure.urgent(payload, cfg["alarm_limits"])
                for r in reasons:
                    print("  [LARM]     " + r)
                # The window is counted from the START of the cycle that last
                # sent. Counted from when the send finished, two 60 s cycles
                # came to 118 s, the 120 s window was missed by two seconds,
                # and the board sent every third minute instead of every
                # second.
                if reasons or started - last_send >= cfg["send_interval_s"]:
                    if flush(buf, cfg):
                        last_send = started
                else:
                    print("  [sandning] vantar, %d i bufferten" % buf.pending())
                buf.prune()
        except Exception as exc:
            # EL-05: log it, and try again next cycle.
            print("  OVANTAT FEL:")
            sys.print_exception(exc)
        wait = max(0, cfg["measure_interval_s"] - (time.time() - started))
        print("  [sover]    %d s\n" % wait)
        time.sleep(wait)


main()
