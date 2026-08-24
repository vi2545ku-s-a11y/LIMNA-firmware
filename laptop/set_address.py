#!/usr/bin/env python3
"""
LIMNA - change the bus address of one sensor.

Every sensor ships on a factory address and has to be moved to the address
the buoy expects before anything else can work. Two pairs in this profile
ship on the SAME address as each other, so this is not an optional tidying
step: wire both in at once and neither can be reached.

    python3 set_address.py --list          what has to be moved, and where to
    python3 set_address.py --scan          who is answering on the bus now
    python3 set_address.py --sensor ec     move one sensor
    python3 set_address.py --sensor ec --dry-run    say what it would write

ONE SENSOR AT A TIME. Connect a single sensor, move it, disconnect it, then
the next. The tool refuses to write while more than one device is answering,
because on a shared address a write goes to both of them.

Rehearse the whole thing without hardware:

    python3 fake_sensors.py --factory      in one window
    python3 set_address.py --scan          in another

The simulated sensors really do change address, so a rehearsal that works is
evidence the sequence is right -- not evidence that the register numbers are.
Those come from the datasheets and are marked OVERIFIERAT until somebody has
done this to a real sensor.

Requires:  pip install pymodbus
"""

import argparse
import os
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

# poller.py sits beside this file and points sensor_profiles at the right
# config when it is imported, so the tool talks to the bus exactly as the
# buoy does.
import poller

# Asking an address where nothing is expected to answer is the NORMAL case
# here -- that is what scanning is. pymodbus logs each silent address as an
# error, which fills the screen with "Unable to decode frame" while the tool
# is working exactly as intended. Quieten it, so that what is printed below
# is the tool's own account and nothing else.
try:
    from pymodbus import pymodbus_apply_logging_config
    pymodbus_apply_logging_config("CRITICAL")
except Exception:
    pass

PROFILE = sensor_profiles.load_profile()
SPECS = sensor_profiles.addressing_specs(PROFILE)

# Modbus RTU reserves 0 for broadcast and stops at 247.
LAGSTA_ADRESS = 1
HOGSTA_ADRESS = 247


def spec_for(key):
    for spec in SPECS:
        if spec["key"] == key:
            return spec
    valbara = ", ".join(s["key"] for s in SPECS)
    raise SystemExit(f"Okand givare '{key}'. Valjbara: {valbara}.")


def collides_with(spec):
    """Other sensors that ship on the same factory address as this one."""
    return [s["label"] for s in SPECS
            if s["key"] != spec["key"]
            and s["factory_unit"] == spec["factory_unit"]]


def build_config(args):
    """The poller's own config, with any command line overrides applied.

    Reusing poller.load_config() and poller.open_client() rather than opening
    a port here means the tool talks to the bus exactly the way the buoy will.
    If one of them works and the other does not, the difference is the wiring
    and not two slightly different pieces of code.
    """
    cfg = poller.load_config()
    modbus = dict(cfg.get("modbus", {}))
    if args.transport:
        modbus["transport"] = args.transport
    if args.port:
        modbus["serial_port"] = args.port
    if args.host:
        modbus["host"] = args.host
    if args.tcp_port:
        modbus["port"] = args.tcp_port
    # A short timeout: this tool asks many questions of addresses where
    # nothing is expected to answer, and every one of them costs the full
    # timeout. The poller keeps its own, longer one.
    modbus["timeout_s"] = args.timeout
    cfg["modbus"] = modbus
    return cfg


def open_bus(cfg):
    client = poller.open_client(cfg)
    if not client.connect():
        raise SystemExit(
            f"Kan inte oppna bussen ({poller.bus_description(cfg)}).\n"
            f"Kontrollera kabeln, att omvandlaren sitter i, och att inget "
            f"annat program haller porten upptagen.")
    return client


def answers(client, adress, register):
    """Does anything answer on this address, at this register?

    A sensor that is present answers even before it has warmed up -- the
    value is not to be trusted yet, but the reply is. So this asks about
    PRESENCE only, and never looks at the number.
    """
    try:
        rr = client.read_holding_registers(register, count=1, slave=adress)
        return not rr.isError()
    except Exception:
        return False


def scan(client):
    """Ask every address this profile could plausibly use who is there.

    Not all 247: at 9600 baud a full sweep with a timeout on every silent
    address takes minutes. The interesting ones are the factory addresses and
    the operating addresses, which is where a sensor of ours can be.

    Each address is asked about the register belonging to the sensor that is
    SUPPOSED to be there. Something else entirely, sitting on the same
    address with a different register layout, therefore reads as silent --
    say so rather than promising more than the method can deliver.
    """
    kandidater = {}
    for spec in SPECS:
        kandidater.setdefault(spec["factory_unit"], spec)
        kandidater.setdefault(spec["unit"], spec)

    hittade = []
    for adress in sorted(kandidater):
        spec = kandidater[adress]
        if answers(client, adress, spec["value_addr"]):
            hittade.append((adress, spec))
    return hittade


def cmd_list():
    print(f"Sensorprofil: {PROFILE['id']}  ({PROFILE['namn']})")
    print(f"Adressregistren ar {PROFILE['adressregister_status']}.\n")
    print(f"  {'givare':<14} {'modell':<12} {'fran':>5} {'till':>5}  "
          f"{'register':>8}  anmarkning")
    for spec in SPECS:
        krock = collides_with(spec)
        not_ = ""
        if krock:
            not_ = f"KROCKAR med {', '.join(krock)}"
        print(f"  {spec['label']:<14} {spec['modell']:<12} "
              f"{spec['factory_unit']:>5} {spec['unit']:>5}  "
              f"0x{spec['address_register']:04X}  {not_}")

    kabel = [s for s in SPECS if s["kabel"]]
    if kabel:
        print("\n  Kabelvarningar\n")
        for spec in kabel:
            print(f"    {spec['label']:<14} {spec['kabel']}")

    print("\n  Ordning: koppla in EN givare, flytta den, koppla ur den, "
          "ta nasta.")
    print("  De som krockar maste flyttas var for sig -- pa en delad adress")
    print("  gar en skrivning till bada.")


def cmd_scan(cfg):
    client = open_bus(cfg)
    try:
        print(f"Bussen: {poller.bus_description(cfg)}\n")
        hittade = scan(client)
    finally:
        client.close()

    if not hittade:
        print("  Ingen givare svarade.")
        print("  Antingen sitter ingen inkopplad, eller sa svarar den pa en")
        print("  adress som inte finns i profilen. Tva givare pa samma "
              "adress")
        print("  ser ocksa ut sa har: de sander samtidigt och svaret blir "
              "olasligt.")
        return

    print(f"  {'adress':>6}  formodad givare")
    for adress, spec in hittade:
        var = "driftadress" if adress == spec["unit"] else "fabriksadress"
        print(f"  {adress:>6}  {spec['label']:<14} ({var})")

    if len(hittade) > 1:
        print(f"\n  {len(hittade)} givare svarar. Koppla ur alla utom en "
              f"innan du flyttar nagon.")


def cmd_set(cfg, args):
    spec = spec_for(args.sensor)
    fran = args.from_addr if args.from_addr is not None else spec["factory_unit"]
    till = args.to_addr if args.to_addr is not None else spec["unit"]

    for namn, adress in (("fran", fran), ("till", till)):
        if not LAGSTA_ADRESS <= adress <= HOGSTA_ADRESS:
            raise SystemExit(
                f"Adressen {adress} ({namn}) ligger utanfor "
                f"{LAGSTA_ADRESS}-{HOGSTA_ADRESS}, vilket ar vad Modbus RTU "
                f"tillater.")

    if fran == till:
        raise SystemExit(
            f"Fran och till ar samma adress ({fran}). Ingenting att gora.\n"
            f"Kor --scan for att se var givarna faktiskt star.")

    register = spec["address_register"]

    print(f"Givare:      {spec['label']}  ({spec['modell']})")
    print(f"Bussen:      {poller.bus_description(cfg)}")
    print(f"Flyttar:     adress {fran}  ->  adress {till}")
    print(f"Skriver:     varde {till} till register 0x{register:04X}")
    print(f"Kontroll:    laser 0x{spec['value_addr']:04X} pa adress {till} "
          f"efterat\n")

    print(f"  VARNING: adressregistret ar {PROFILE['adressregister_status']}.")
    print(f"  Databladen ger tre olika register for de fem givarna. "
          f"Skriver du till")
    print(f"  fel register hander i basta fall ingenting, i varsta fall "
          f"staller du om")
    print(f"  nagot annat. Kontrollera mot databladets sida forst.")

    krock = collides_with(spec)
    if krock:
        print(f"\n  Denna givare delar fabriksadress {spec['factory_unit']} "
              f"med {', '.join(krock)}.")
        print(f"  Bada maste vara urkopplade utom den du flyttar.")

    if spec["kabel"]:
        print(f"\n  Kabel: {spec['kabel']}")

    if args.dry_run:
        print("\n  TORRKORNING -- ingenting skrivs.")
        return

    client = open_bus(cfg)
    try:
        # Who is actually on the bus? A write on a shared address reaches
        # every device sitting on it, so this is the one check that has to
        # happen before anything is written and not after.
        print("\n  Kontrollerar vem som svarar...")
        hittade = scan(client)
        adresser = [a for a, _ in hittade]

        if len(hittade) > 1:
            raise SystemExit(
                f"\n  {len(hittade)} givare svarar (adress "
                f"{', '.join(str(a) for a in adresser)}).\n"
                f"  Ingenting skrivs. Koppla ur alla utom den du flyttar.\n"
                f"  En skrivning pa en delad adress gar till bada givarna.")

        if fran not in adresser:
            svarar = (f"adress {adresser[0]}" if adresser
                      else "ingen adress alls")
            raise SystemExit(
                f"\n  Ingen givare svarar pa adress {fran} -- {svarar} "
                f"svarar.\n"
                f"  Ingenting skrivs. Kontrollera kabeln, matningen, och att "
                f"givaren\n"
                f"  verkligen star pa {fran}. Kor --scan for att se bussen.")

        print(f"  Adress {fran} svarar, och ingen annan. Bra.")

        if not args.yes:
            svar = input(f"\n  Skriv {till} till 0x{register:04X} pa adress "
                         f"{fran}?  Skriv ja for att fortsatta: ")
            if svar.strip().lower() not in ("ja", "j"):
                print("  Avbrutet. Ingenting skrevs.")
                return

        try:
            rr = client.write_register(register, till, slave=fran)
        except Exception as exc:
            raise SystemExit(f"\n  Skrivningen misslyckades: "
                             f"{type(exc).__name__}: {exc}")
        if rr.isError():
            raise SystemExit(
                f"\n  Givaren avvisade skrivningen: {rr}\n"
                f"  Det ar oftast fel adressregister. Kontrollera "
                f"databladet.")

        print(f"  Skrivet.")

        # Did it take? The proof is not that the write was acknowledged --
        # it is that the sensor has actually moved: the new address answers
        # and the old one has gone quiet.
        print("\n  Kontrollerar...")
        nytt_svar = answers(client, till, spec["value_addr"])
        gammalt_svar = (answers(client, fran, spec["value_addr"])
                        if fran != till else nytt_svar)

        if nytt_svar and not gammalt_svar:
            print(f"  Adress {till} svarar. Adress {fran} ar tyst.")
            print(f"  {spec['label']} star nu pa {till}.")
        elif nytt_svar and gammalt_svar:
            print(f"  Bade {fran} och {till} svarar.")
            print(f"  Antingen kraver givaren en stromcykel innan den nya")
            print(f"  adressen galler, eller sa sitter tva givare pa bussen.")
            print(f"  Bryt strommen, koppla upp igen och kor --scan.")
        else:
            print(f"  Adress {till} svarar inte.")
            print(f"  Skrivningen kvitterades men givaren flyttade inte dit.")
            print(f"  Sannolikt fel adressregister, eller sa behover givaren")
            print(f"  en stromcykel. Kor --scan efter en stromcykel innan du")
            print(f"  skriver nagot mer.")
    finally:
        client.close()


def main():
    p = argparse.ArgumentParser(
        description="Andrar bussadress pa en LIMNA-givare, en i taget.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Kor --list forst. Den visar vad som ska flyttas och vart.")
    p.add_argument("--list", action="store_true",
                   help="visa vilka givare som ska flyttas, och vart")
    p.add_argument("--scan", action="store_true",
                   help="visa vilka adresser som svarar just nu")
    p.add_argument("--sensor", metavar="NYCKEL",
                   help="givaren som ska flyttas (do, turb, ec, ph, lvl)")
    p.add_argument("--from", dest="from_addr", type=int, metavar="ADRESS",
                   help="adressen givaren star pa nu (standard: "
                        "fabriksadressen ur profilen)")
    p.add_argument("--to", dest="to_addr", type=int, metavar="ADRESS",
                   help="adressen den ska fa (standard: driftadressen ur "
                        "profilen)")
    p.add_argument("--dry-run", action="store_true",
                   help="visa vad som skulle skrivas, skriv ingenting")
    p.add_argument("--yes", action="store_true",
                   help="hoppa over fragan innan skrivning")
    p.add_argument("--transport", choices=("tcp", "rtu"),
                   help="overstyr transporten i config.json")
    p.add_argument("--port", metavar="ENHET",
                   help="serieport for rtu, t.ex. /dev/ttyUSB0")
    p.add_argument("--host", metavar="VARD", help="vard for tcp")
    p.add_argument("--tcp-port", type=int, metavar="PORT",
                   help="port for tcp")
    p.add_argument("--timeout", type=float, default=0.5, metavar="SEK",
                   help="svarstid per forfragan (standard 0.5 s)")
    args = p.parse_args()

    if args.list:
        cmd_list()
        return
    if args.scan:
        cmd_scan(build_config(args))
        return
    if args.sensor:
        cmd_set(build_config(args), args)
        return

    p.print_help()
    print("\nBorja med:  python3 set_address.py --list")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAvbrutet.")
        sys.exit(1)
