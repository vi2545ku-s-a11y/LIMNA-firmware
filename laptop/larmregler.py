"""Build the six LIMNA alert rules in Grafana via the provisioning API.

Requirements covered: F-10 (fixed threshold), F-11 (rate of change),
F-13 (silent sensor, low battery, thermometers disagreeing).

Six, counted from RULES below. This docstring said four and etapper.md said
five until 2026-08-10; the file has had six since the old "Sensor svarar
inte" rule was split into a measurement gap and a total silence.

SECOND LINE OF DEFENCE, NOT THE ONLY ONE
----------------------------------------
Since 2026-08-10 the buoy carries its own copy of the fixed limits, in
alarm_limits in config.json, and uses them to decide whether a measurement
has to go out at once instead of at the next send window (F-12). These rules
stay exactly as they are. The two do different jobs:

    the buoy      decides WHEN to send. It has no history and no view of
                  other buoys, but it is the only one that can shorten the
                  delay between a limit going and somebody hearing about it.
    these rules   decide who gets woken up, keep the alarm history, and
                  catch what the buoy cannot see about itself: rate of
                  change over hours, and silence.

If the buoy is wrong, the server still alarms. If the server is down, the
measurement still went out early. Keep the numbers in the two places in
step -- 'Lagt syre' below and "do" in alarm_limits are the same 5.0 mg/L,
and 'Lagt batteri' and 'Temperaturgivarna oense' likewise.
"""
import json
import os
import urllib.request

GRAFANA = os.environ.get("GRAFANA_URL", "http://localhost:3000")

# GRAFANA'S PASSWORD IS NOT WRITTEN DOWN HERE.
#
# It used to be, as the literal "admin:limna", which made .gitignore's care
# over .env pointless: the same password stood in a file that WAS versioned.
# The variable names are the ones .env and docker-compose.yml already use, so
# there is still only one place the value lives.
#
#     set -a; source .env; set +a
#     python3 larmregler.py
def _auth():
    user = os.environ.get("GRAFANA_USER")
    password = os.environ.get("GRAFANA_PASSWORD")
    if not user or not password:
        raise SystemExit(
            "GRAFANA_USER och GRAFANA_PASSWORD saknas i miljon.\n"
            "De star i .env, som inte lases automatiskt av python. Kor:\n"
            "    set -a; source ../.env; set +a\n"
            "    python3 larmregler.py")
    return f"{user}:{password}"

FOLDER = "afthq18x9kzy8b"
DS = "limna-influx"

EXPR_DS = {"type": "__expr__", "uid": "__expr__"}


# ---------------------------------------------------------------------------
# INTERVALS -- ONE SOURCE, AND IT IS config.json
# ---------------------------------------------------------------------------
# These used to be written out here as 3600 and 21600 while config.json said
# 120, so the rules were built against numbers the buoy did not use. Two
# rules below depend on them directly: a silence rule can never be tighter
# than the send interval, because between two sends a perfectly healthy buoy
# has delivered nothing at all.
#
# The fallbacks are the PRODUCTION values, so a missing config.json (it is
# gitignored) gives sane rules rather than none. config.json normally holds
# the development values, which are much shorter -- main() says so out loud
# before it builds anything, because rules built against a 60 s interval will
# fire on the real buoy every quarter of an hour.
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")

PRODUCTION = {
    "measure_interval_s": 900,          # 15 min
    "send_interval_s": 3600,            # 1 h
    "eco_send_interval_s": 21600,       # 6 h, eco mode (F-02)
}


def load_intervals():
    try:
        with open(CONFIG_FILE) as fh:
            cfg = json.load(fh)
    except Exception:
        return dict(PRODUCTION), False
    intervals = {key: cfg.get(key, PRODUCTION[key]) for key in PRODUCTION}
    return intervals, True


INTERVALS, FROM_CONFIG = load_intervals()
MEASURE_INTERVAL_S = INTERVALS["measure_interval_s"]
SEND_INTERVAL_S = INTERVALS["send_interval_s"]
ECO_SEND_INTERVAL_S = INTERVALS["eco_send_interval_s"]

# Three missed measurements in a row. Derived rather than written down, so it
# follows the measurement interval instead of contradicting it.
GAP_S = 3 * MEASURE_INTERVAL_S

# Two eco intervals plus an hour of margin: the tightest honest value for
# "nothing at all has arrived" that does not cry wolf.
#
# ECO MODE IS NOT IMPLEMENTED. F-02 exists as a requirement and as this
# number; the poller has no eco mode yet, so today the buoy always sends at
# send_interval_s. The rule is built for the slowest mode the buoy may end up
# in, which is the safe direction: it is late rather than wrong.
SILENCE_S = 2 * ECO_SEND_INTERVAL_S + 3600


def flux(field, start="-15m", stop="now()"):
    return (f'from(bucket:"limna")\n'
            f'  |> range(start: {start}, stop: {stop})\n'
            f'  |> filter(fn: (r) => r._measurement == "limna")\n'
            f'  |> filter(fn: (r) => r._field == "{field}")')


def flux_lucka(field, start="-6h"):
    """Largest gap, in seconds, between two consecutive MEASUREMENTS.

    The timestamp on a point is the one the buoy wrote, not the one the
    server received it at, so this measures whether the buoy kept measuring
    -- not whether it kept sending. A buoy that measures every 15 minutes but
    only reports once an hour shows no gap at all once the batch lands. A
    buoy whose sensor rail died leaves a hole that never fills in.

    elapsed() puts the gap in its own column, so it has to be moved into
    _value before Grafana's reduce stage can see it.
    """
    return (f'from(bucket:"limna")\n'
            f'  |> range(start: {start})\n'
            f'  |> filter(fn: (r) => r._measurement == "limna")\n'
            f'  |> filter(fn: (r) => r._field == "{field}")\n'
            f'  |> elapsed(unit: 1s)\n'
            f'  |> map(fn: (r) => ({{_time: r._time, '
            f'_value: float(v: r.elapsed)}}))\n'
            f'  |> max()')


def flux_alder(field, start="-24h"):
    """Age in seconds of the newest measurement the server has received.

    This is the only thing that catches a buoy that has gone completely
    quiet, and it cannot be faster than the send interval -- until the next
    batch is due, silence and death look identical from here. That is a cost
    of batching, not a flaw in the rule.
    """
    return (f'from(bucket:"limna")\n'
            f'  |> range(start: {start})\n'
            f'  |> filter(fn: (r) => r._measurement == "limna")\n'
            f'  |> filter(fn: (r) => r._field == "{field}")\n'
            f'  |> last()\n'
            f'  |> map(fn: (r) => ({{_time: r._time, _value: '
            f'float(v: int(v: now()) - int(v: r._time)) / 1000000000.0}}))')


def query(ref, field, from_s, to_s, start, stop):
    """One InfluxDB query stage."""
    return query_raw(ref, flux(field, start, stop), from_s, to_s)


def query_raw(ref, flux_text, from_s, to_s):
    """Same, but for a query built by one of the helpers above."""
    return {
        "refId": ref,
        "queryType": "",
        "relativeTimeRange": {"from": from_s, "to": to_s},
        "datasourceUid": DS,
        "model": {
            "refId": ref,
            "query": flux_text,
            "datasource": {"type": "influxdb", "uid": DS},
            "intervalMs": 1000,
            "maxDataPoints": 43200,
        },
    }


def reduce(ref, src, reducer="last"):
    return {
        "refId": ref,
        "queryType": "",
        "relativeTimeRange": {"from": 10800, "to": 0},
        "datasourceUid": "__expr__",
        "model": {
            "type": "reduce", "refId": ref, "expression": src,
            "reducer": reducer, "settings": {"mode": "dropNN"},
            "datasource": EXPR_DS,
        },
    }


def math(ref, expression):
    return {
        "refId": ref,
        "queryType": "",
        "relativeTimeRange": {"from": 10800, "to": 0},
        "datasourceUid": "__expr__",
        "model": {"type": "math", "refId": ref, "expression": expression,
                  "datasource": EXPR_DS},
    }


def threshold(ref, src, op, value):
    return {
        "refId": ref,
        "queryType": "",
        "relativeTimeRange": {"from": 10800, "to": 0},
        "datasourceUid": "__expr__",
        "model": {
            "type": "threshold", "refId": ref, "expression": src,
            "conditions": [{"evaluator": {"type": op, "params": [value]}}],
            "datasource": EXPR_DS,
        },
    }


def rule(title, krav, data, condition, for_, summary, no_data="NoData"):
    return {
        "title": title,
        "ruleGroup": "limna-larm",
        "folderUID": FOLDER,
        "condition": condition,
        "data": data,
        "noDataState": no_data,
        "execErrState": "Error",
        "for": for_,
        "orgID": 1,
        "isPaused": False,
        "annotations": {"summary": summary, "krav": krav},
        "labels": {"boj": "HV-001", "krav": krav},
    }


RULES = [
    # F-10 - fixed limit. Oxygen below 5.0 mg/L sustained for 15 minutes.
    rule("Lagt syre", "F-10",
         [query("A", "do", 900, 0, "-15m", "now()"),
          reduce("B", "A", "last"),
          threshold("C", "B", "lt", 5.0)],
         "C", "15m",
         "Loest syre under 5,0 mg/L i 15 minuter. Risk for fiskdod."),

    # F-11 - rate of change. Turbidity up more than 200 % against a
    # baseline window three hours back. Two queries, then math.
    rule("Snabb turbiditetsokning", "F-11",
         [query("A", "turb", 900, 0, "-15m", "now()"),
          query("B", "turb", 10800, 9900, "-3h", "-2h45m"),
          reduce("C", "A", "mean"),
          reduce("D", "B", "mean"),
          math("E", "($C - $D) / $D * 100"),
          threshold("F", "E", "gt", 200.0)],
         "F", "5m",
         "Turbiditeten har okat mer an 200 procent pa tre timmar. "
         "Tyder pa avrinning eller uppgrumling."),

    # F-13 - THE BUOY STOPPED MEASURING.
    #
    # This replaces the old "no data in the last 45 minutes" rule, which was
    # unusable once measurements started going out in batches: between two
    # sends a perfectly healthy buoy has delivered nothing, and that rule
    # could not tell the difference. It would have fired every single hour.
    #
    # A point's timestamp is the one the BUOY wrote, so a gap in the series
    # means the buoy did not measure -- not that it did not report. Batching
    # delays when this fires, but never causes it to fire wrongly. Three
    # missed measurements in a row at the real 15 minute interval.
    #
    # In development it will also fire when the laptop sleeps, because the
    # poller really does skip cycles then. That is the rule working.
    rule("Matningar saknas", "F-13",
         [query_raw("A", flux_lucka("turb", "-6h"), 21600, 0),
          reduce("B", "A", "max"),
          threshold("C", "B", "gt", float(GAP_S))],
         "C", "5m",
         f"Glapp pa over {GAP_S / 60:.0f} minuter mellan tva matningar. "
         f"Bojen mater inte som den ska -- sensorrail, pollare eller strom."),

    # F-13 - THE BUOY HAS GONE COMPLETELY QUIET.
    #
    # The other half of the split. Nothing at all has arrived, which is a
    # power, modem or coverage failure rather than a measurement failure.
    #
    # This one CANNOT be fast, and the threshold says why: until the next
    # batch is due, a silent buoy and a dead buoy look exactly alike from
    # the server. Two eco-mode intervals plus an hour of margin is the
    # tightest honest value that does not cry wolf six times a day.
    rule("Bojen har inte hort av sig", "F-13",
         [query_raw("A", flux_alder("turb", "-24h"), 86400, 0),
          reduce("B", "A", "last"),
          threshold("C", "B", "gt", float(SILENCE_S))],
         "C", "10m",
         f"Inget har kommit fran bojen pa over {SILENCE_S / 3600:.0f} timmar. "
         f"Strom, modem eller tackning. Larmet kan inte ga snabbare an "
         f"sandintervallet.",
         no_data="Alerting"),

    # F-13 - low battery.
    rule("Lagt batteri", "F-13",
         [query("A", "batt", 1800, 0, "-30m", "now()"),
          reduce("B", "A", "last"),
          threshold("C", "B", "lt", 12.4)],
         "C", "10m",
         "Batterispanningen under 12,4 V. Bojen gar snart i energisparlage."),

    # F-13 - a sensor that drifts rather than fails. Four thermometers in the
    # same water disagree by about 0.2 C from calibration error alone. Above
    # 1.0 C sustained, one of them is walking away from the others, and that
    # is a board going bad -- not weather, not stratification.
    #
    # This is the only rule here that catches a sensor which keeps answering
    # and keeps looking plausible. It exists because the payload carries
    # temp_d, the spread between the four.
    rule("Temperaturgivarna oense", "F-13",
         [query("A", "temp_d", 1800, 0, "-30m", "now()"),
          reduce("B", "A", "mean"),
          threshold("C", "B", "gt", 1.0)],
         "C", "30m",
         "Spridningen mellan de fyra temperaturgivarna over 1,0 C. "
         "En givare driver. Kontrollera kalibreringen."),
]


def post(rule_body):
    req = urllib.request.Request(
        f"{GRAFANA}/api/v1/provisioning/alert-rules",
        data=json.dumps(rule_body).encode(),
        headers={"Content-Type": "application/json",
                 "X-Disable-Provenance": "true"},
        method="POST")
    import base64
    req.add_header("Authorization", "Basic " +
                   base64.b64encode(_auth().encode()).decode())
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


# Rules this script used to create and no longer should. Left behind they keep
# evaluating, and this one in particular would fire every hour on a healthy
# buoy once sending became less frequent than its 45 minute window.
UTGANGNA = {
    "Sensor svarar inte":
        "ersatt av 'Matningar saknas' + 'Bojen har inte hort av sig'",
}


def existing_rules():
    """Title -> uid for what is already in Grafana, so re-runs stay quiet."""
    req = urllib.request.Request(f"{GRAFANA}/api/v1/provisioning/alert-rules")
    import base64
    req.add_header("Authorization", "Basic " +
                   base64.b64encode(_auth().encode()).decode())
    try:
        with urllib.request.urlopen(req) as resp:
            return {r["title"]: r["uid"] for r in json.load(resp)}
    except Exception:
        return {}


def delete(uid):
    req = urllib.request.Request(
        f"{GRAFANA}/api/v1/provisioning/alert-rules/{uid}",
        headers={"X-Disable-Provenance": "true"}, method="DELETE")
    import base64
    req.add_header("Authorization", "Basic " +
                   base64.b64encode(_auth().encode()).decode())
    urllib.request.urlopen(req).close()


def main():
    kalla = "config.json" if FROM_CONFIG else "inbyggda produktionsvarden"
    print(f"  Intervall fran {kalla}: matning {MEASURE_INTERVAL_S} s, "
          f"sandning {SEND_INTERVAL_S} s, eko {ECO_SEND_INTERVAL_S} s")
    print(f"  Reglerna byggs mot glapp {GAP_S} s och tystnad {SILENCE_S} s")
    if MEASURE_INTERVAL_S < 900 or SEND_INTERVAL_S < 3600:
        print("  VARNING: det ar utvecklingsintervall, inte driftintervall.")
        print("           Regler byggda mot dem larmar standigt pa en riktig")
        print("           boj. Satt driftvarden i config.json forst, eller")
        print("           rakna med att gora om reglerna.")
    print()

    have = existing_rules()

    for title, varfor in UTGANGNA.items():
        if title in have:
            try:
                delete(have[title])
                print(f"  BORTTAGEN    {title:<28} {varfor}")
            except urllib.error.HTTPError as exc:
                print(f"  FEL          {title:<28} HTTP {exc.code} vid radering")

    for r in RULES:
        if r["title"] in have:
            print(f"  finns redan  {r['title']}")
            continue
        try:
            out = post(r)
            print(f"  SKAPAD       {out['title']:<28} "
                  f"{out['annotations']['krav']}  uid={out['uid']}")
        except urllib.error.HTTPError as exc:
            print(f"  FEL          {r['title']:<28} HTTP {exc.code}")
            print("       ", exc.read().decode()[:400])


# Guarded so the helpers above can be imported without creating the rules
# as a side effect.
if __name__ == "__main__":
    main()
