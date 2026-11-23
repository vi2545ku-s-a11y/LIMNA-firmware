# LIMNA — etappprotokoll

Varje etapp med kommando, förväntat resultat och kontrollpunkt. Bocka av
allteftersom.

| Markering | Betyder |
|---|---|
| ✅ **Verifierad** | Har körts och fungerade, med datum |
| 🚧 **Påbörjad** | Delvis körd, eller körd före en ändring som påverkar den |
| ⬜ **Ej påbörjad** | Inte körd |

Bojen ligger i en bevattningsdamm och har två givare från Seeed: pH, ORP och
temperatur (S-pH&ORP-01A) samt grumlighet och temperatur (S-DTS210-01).
Givarna byttes 2026-11-23; etapper som kördes före det är körda mot de gamla
syre- och nivågivarna. Detaljer om givarna står i `sensor_databla.md`,
manualerna i `docs/datablad/`, firmwaren i `firmware/README.md`.

---

## Nuläge

| Etapp | Status |
|---|---|
| 0 Docker | 🚧 körd före 2026-11-09, inte efter |
| 1 Självtester | ✅ 2026-11-23, med de nya givarna |
| 2 Dataplattformen | 🚧 inte körd efter givarbytet |
| 3 Första mätvärdet | 🚧 simulator → pollare verifierad 2026-11-23 med de nya givarna, MQTT → Grafana inte |
| 4 Sensorfel | ⬜ |
| 5 Buffring | 🚧 logiken verifierad i självtestet, inte mot broker |
| 6 Larm | ⬜ fem regler, inte skapade i Grafana än |
| 7 Dashboarden | 🚧 sju paneler i `limna.json` för pH, ORP och grumlighet, inte laddad i Grafana efter ändringen |
| 8 Hårdvaran | 🚧 adressbytet repeterat i simulatorn 2026-11-23, givarna inte inkopplade |

---

## ETAPP 0 — Docker 🚧

```bash
docker --version
docker compose version
```

**KONTROLL:** båda svarar med en version.

---

## ETAPP 1 — Självtester ✅ 2026-11-23

```bash
make selftest
```

Kör tre test i följd:

1. **Profilen** (`delad/sensor_profiles.py`): registerkartan för pH, ORP och
   grumlighet, de två termometrarna, strömkanaler, matning, fabriksadresser,
   kabelvarningar och kontrollen att simulatorn och pollaren ser samma karta.
2. **Simulatorn** (`laptop/fake_sensors.py`): ett dygn i dammen, ett regn,
   uppvärmningen för pH och grumlighet, varifrån uppvärmningstiderna kommer,
   signerad avkodning av ORP och fabriksadresserna.
3. **Pollaren** (`laptop/poller.py`): bufferten, nyttolasten, larmgränserna,
   median av tre avläsningar, fastfruset värde och avkodningen mot
   manualernas egna exempelsvar.

**KONTROLL:**

- [x] Kartkontrollen säger OK på alla fem rader.
- [x] pH toppar på eftermiddagen och bottnar före gryningen.
- [x] Grumligheten stiger 05–08 när det bevattnas, och kraftigt vid regn.
- [x] pH läst efter 0 s är fel med flera enheter, efter 30 s rätt.
- [x] ORP −12,5 mV avkodas rätt som signerat, och blir 6541,1 som osignerat.
- [x] Manualernas exempelsvar avkodas till manualernas värden: pH 7,83,
      ORP 239,5 mV, 98,5 NTU, 25,45 °C och 19,2 °C.
- [x] Båda givarna krockar på fabriksadress 1.
- [x] Nyttolasten är 187 byte.

---

## ETAPP 2 — Dataplattformen 🚧

```bash
cp .env.example .env      # första gången
docker compose up -d
docker compose ps
```

**KONTROLL:**

- [ ] Fyra containrar är igång.
- [ ] Grafana på http://localhost:3000, datakällan InfluxDB svarar.
- [ ] Dashboarden **LIMNA — översikt** har sju paneler: senaste värden, pH,
      grumlighet, ORP, vattentemperatur, termometrarnas skillnad och
      batterispänning.

Har Grafana redan kört den gamla dashboarden: `docker compose restart grafana`.

---

## ETAPP 3 — Första mätvärdet hela vägen 🚧

```bash
make sim      # fönster 1
make poll     # fönster 2
```

Sätt `"warmup_s": 5` och `"measure_interval_s": 60` i `laptop/config.json`
om du inte vill vänta. Sätt tillbaka 30 och 900 efteråt.

**KONTROLL:**

- [x] Pollaren läser pH, ORP, grumlighet och båda termometrarna ur
      simulatorn och bygger nyttolasten med `temp_d` (2026-11-23, utan
      broker).
- [ ] Mätningen skickas till Mosquitto.
- [ ] En punkt per mätning syns i Grafana, med bojens tidsstämpel.

Med för kort uppvärmning läser pH och grumlighet för lågt och flaggas
`brusig`. Det är simuleringen som gör sitt jobb (2 s gav pH 4,49 i stället
för 7,9 vid provet 2026-11-23).

---

## ETAPP 4 — Sensorfel ⬜

Ändra i `laptop/faults.json` medan allt kör:

| Sätt | Förväntat |
|---|---|
| `"sensor_2_turb": "offline"` | `SVARAR INTE efter 3 forsok` för grumligheten, pH och ORP läses som vanligt (EL-03). `temp_d` försvinner |
| `"sensor_1_ph": "noisy"` | medianen tar bort enstaka skräpvärden, raden flaggas `brusig` om alla tre spretar |
| `"sensor_1_ph": "stuck"` | efter `stuck_cycles` mätningar flaggas `"fel":{"ph":"stuck"}`. Sänk till 3 för att se det |
| `"sensor_2_turb": "drift"` | grumligheten stiger sakta medan givaren svarar normalt, som vid påväxt på fönstret |
| `"sensor_1_ph": "drift"` | pH och ORP driver, och pH-givarens termometer följer med så att `temp_d` växer |

**KONTROLL:**

- [ ] Alla beter sig som i tabellen.
- [ ] Inget fel stoppar pollaren.

---

## ETAPP 5 — Buffring vid avbrott 🚧

```bash
docker compose stop mosquitto
# vänta några mätningar
docker compose start mosquitto
```

**KONTROLL:**

- [ ] Mätningarna ligger kvar i bufferten under avbrottet.
- [ ] Allt skickas i efterhand, med ursprungliga tidsstämplar i Grafana (K-03).

---

## ETAPP 6 — Larm ⬜

```bash
cd laptop
set -a; source ../.env; set +a
python3 larmregler.py
```

| Larm | Regel |
|---|---|
| pH utanför gränserna | `ph` utanför 6,0–9,0 i 15 min (PLACEHOLDER) |
| Grumligt vatten | `turb` över 50 NTU i 30 min (PLACEHOLDER) |
| Mätningar saknas | glapp över tre mätintervall |
| Bojen har inte hört av sig | inget levererat på två ekointervall plus en timme |
| Lågt batteri | `batt` under 12,4 |

**KONTROLL:**

- [ ] Fem regler finns under Alerting, och den gamla "Lagt syre" är borta.
- [ ] `"bloom": true` i `faults.json` utlöser pH- och grumlighetslarmet.
- [ ] `"rain_event": true` ger en grumlighetstopp som klingar av.
- [ ] Batteri med `"source": "sim"` och snabb urladdning utlöser lågt batteri
      och energisparläge vid 13,0 V.

---

## ETAPP 7 — Dashboarden 🚧

Karta med bojens position, grafer med valbar period, batteri- och
kommunikationsstatus, larmhistorik och CSV-export. Spara som JSON i
`grafana/dashboards/limna.json`.

- [ ] Positionen i `config.json` ersatt med dammens riktiga.
- [ ] Panelerna för pH, grumlighet, ORP och `temp_d` visar data.

---

## ETAPP 8 — När hårdvaran kommer ⬜

I denna ordning, med bara en givare inkopplad åt gången:

Ingen baud behöver ändras: båda givarna levereras på 9600.

1. **Byt transport** i `laptop/config.json`: `"transport": "rtu"`,
   `"serial_port"` för USB–RS485-adaptern. Mata givarna med reglerade 12 V.
2. **Grumlighetsgivaren ensam:** `python3 set_address.py --scan`. Svarar den
   på 1 eller 50? Skriv upp vilket och rätta `factory_unit` i profilen. Flytta
   den till adress 2: `python3 set_address.py --sensor turb` (med `--from 50`
   om det behövs).
3. **pH-givaren:** koppla in den. Den står redan rätt på adress 1. `--scan`
   ska nu visa 1 och 2.
4. **Läs båda givarna från laptopen** med `make poll`, innan ESP32:n kopplas in.
5. **Mät uppvärmningstiden:** slå på strömmen, läs varje sekund, se när
   värdet slutar röra sig. Skriv in den i profilen med källa `uppmatt`.
6. **Firmwaren:** följ `firmware/README.md`, steg 1–7.

**KONTROLL:**

- [x] Adressbytet repeterat i simulatorn (`fake_sensors.py --factory`,
      pH-givaren offline): grumlighetsgivaren flyttad 1 → 2, därefter svarar
      båda (2026-11-23).
- [ ] Grumlighetsgivarens riktiga fabriksadress är fastställd (1 eller 50).
- [ ] Båda givarna svarar på sina driftadresser på 9600 baud.
- [ ] pH visar 4,00 och 7,00 i buffertlösning, grumligheten stiger när vattnet
      rörs upp.
- [ ] Uppvärmningstiden är uppmätt för båda.
- [ ] Strömmen är uppmätt för båda.

---

## Kvar att göra som saknar test helt

- Larmgränser för pH, ORP och grumlighet, satta tillsammans med bonden.
- Kalibreringsrutin för pH-elektroden (en gång i månaden enligt manualen) och
  vinterförvaring i 3 M KCl.
- Energibudgeten omräknad för dammbojen med uppmätta värden.
- TLS och lösenord mot brokern (K-02) före första piloten.
