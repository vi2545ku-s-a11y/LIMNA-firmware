# LIMNA — sensorernas datablad

Sammanställt 23 november 2026 för bojen i bevattningsdammen. Siffrorna är
**hämtade ur tillverkarens manualer**, inte uppskattade. Där manualen inte
säger något står det så. Ingen av givarna är ännu provad mot riktig
hårdvara.

| Enhet | Sensor | Modell | Manual | Status |
|---|---|---|---|---|
| 1 | pH, ORP och temperatur | Seeed SenseCAP S-pH&ORP-01A, SKU 100050803 | [`docs/datablad/Seeed_S-pH-ORP-01A_manual.pdf`](docs/datablad/Seeed_S-pH-ORP-01A_manual.pdf), V1.0 | Registerkarta ur manualen |
| 2 | Grumlighet och temperatur | Seeed SenseCAP S-DTS210-01, SKU 101990793 | [`docs/datablad/Seeed_S-DTS210-01_manual.pdf`](docs/datablad/Seeed_S-DTS210-01_manual.pdf), V1.0.0, 2025-01-21 | Registerkarta ur manualen, **fabriksadressen motsägelsefull** |

Produktsidor: [pH/ORP](https://www.seeedstudio.com/RS485-pH-ORP-and-Temperature-Sensor-p-6945.html),
[grumlighet](https://www.seeedstudio.com/RS485-p-5102.html).

---

## 1. pH, ORP och temperatur — Seeed S-pH&ORP-01A

| | |
|---|---|
| Mätprincip | pH-glaselektrod och ORP-elektrod, gelelektrolyt, PTFE-diafragma |
| pH | 0,00–14,00, upplösning 0,01, noggrannhet ±0,1, svarstid < 10 s |
| ORP | −2000,0 till 2000,0 mV, upplösning 0,1 mV, noggrannhet ±20 mV, svarstid < 30 s |
| Temperatur | −40 till 80 °C, upplösning 0,1 °C, noggrannhet ±0,5 °C |
| Matning | 4,5–28 V DC |
| Ström | under 8 mA vilande, under 10 mA vid mätning, vid 12 V |
| Skydd | IP68, 3/4" NPT-gänga, max 0,2 MPa |
| Drift | 0–80 °C |
| Kabel | 2 m, med kabelhylsor |
| Mått | 207 × 30 mm |
| Buss | Modbus RTU över RS485 |
| Fabriksinställning | adress **1**, **9600 baud**, 8N1 |

**Ledare**

| Färg | Funktion |
|---|---|
| Röd | + matning |
| Svart | GND |
| **Gul** | **RS485 A+** |
| **Vit** | **RS485 B−** |

**Register som läses** (funktion 0x03 eller 0x04, alla INT16)

| Register | Värde | Skala |
|---|---|---|
| 0x0000 | Temperatur, kalibrerad (med offset från 0x0021) | ÷ 100 → °C |
| 0x0001 | pH, temperaturkompenserat till 25 °C | ÷ 100 |
| 0x0002 | ORP, temperaturkompenserat | ÷ 10 → mV, **signerat** |

Manualens exempel: frågan `01 03 00 00 00 0A C5 CD` gav `09 F1 03 0F 09 5B …`,
alltså 25,45 °C, pH 7,83 och 239,5 mV. Pollarens självtest kontrollerar att
profilen avkodar just de bytena till just de talen.

**ORP kan bli negativt** i vatten utan syre. Läses registret som osignerat
blir −12,5 mV till 6541,1 mV.

**Register som finns men inte läses**

| Register | Vad | Varför inte |
|---|---|---|
| 0x0003, 0x0005, 0x0006, 0x0008 | Elektrodernas råa mV | pH och ORP räcker |
| 0x0004, 0x0007 | pH och ORP utan temperaturkompensering | vi vill ha kompenserat |
| 0x0009 | Temperatur utan offset | 0x0000 har offset inlagd |
| 0x1000–0x1013 | Samma värden som flyttal | ordningen styrs av 0x0023 och är från fabrik **CDAB** (lågt ord först). Heltalen har full upplösning utan den fällan. |

**Inställningar och kalibrering** (skrivs med 0x06)

| Register | Vad | Fabrik |
|---|---|---|
| 0x0021 | Temperaturoffset, ÷ 100 °C | 0 |
| 0x0022 | pH-kompensering till 25 °C: 0 = på, 1 = av | 0 |
| 0x0024 | Filterstyrka 0–15 | 2 |
| 0x0030–0x0032 | pH-kalibrering: skriv 0x7FFF i buffert 4,00 / 7,00 / 10,01 | — |
| 0x0033 | Kalibreringsgrupp: 0 = 4,00/7,00/10,01, 1 = 4,00/6,86/9,18 | 0 |
| 0x0041 | ORP-kalibrering: skriv standardlösningens mV, t.ex. 256 | — |
| 0x0050 | Återställ kalibrering: 0 = pH, 1 = ORP, 0xFFFF = båda | — |
| 0x0051 | Skriv 0xFFFF = omstart | — |
| **0x0200** | **Adress, 1–247** | **1** |
| 0x0201 | Baud: 0=1200, 1=2400, 2=4800, 3=9600, 4=19200, 5=38400 | 3 |

**Ny adress gäller först efter omstart.** Manualen säger det uttryckligen.
Skriv adressen, bryt strömmen, slå på igen. I praktiken behöver pH-givaren
inte flyttas alls: den ska stå på 1, och det gör den från fabrik.

**Underhåll, enligt manualen**

- pH-elektroden håller 1–2 år, ORP-elektroden 1–3 år.
- Kalibrera pH en gång i månaden. Kontrollera ORP mot +258 mV-standard
  månadsvis; byt eller rengör vid mer än ±30 mV avvikelse.
- En ny eller uttorkad elektrod blötläggs 24 h i 3 M KCl före användning.
- **Över vintern**, när bojen är upptagen: förvara elektroden i 3 M KCl eller
  med torrskyddet på. En elektrod som får torka ut kristalliserar.
- Montera lodrätt. Undvik långvarigt pH under 2 eller över 12.

---

## 2. Grumlighet och temperatur — Seeed S-DTS210-01

| | |
|---|---|
| Mätprincip | 90° spritt ljus, infraröd lysdiod, optisk fiber |
| Grumlighet | 0–1000,0 NTU, upplösning 0,1 NTU, noggrannhet ±5 % av fullt område |
| Temperatur | Pt1000, upplösning 0,1 °C, noggrannhet ±0,3 °C |
| Kalibrering | tvåpunkts: nollpunkt och lutning |
| Matning | **12–24 V DC** (typiskt 12 V) |
| Effekt | 0,3 W vid 12 V |
| Skydd | IP68, 3/4" NPT-gänga, max 0,2 MPa |
| Drift | 0–50 °C |
| Kabel | 10 m, M16-kontakt med 5 poler |
| Mått | 185 × 30 mm |
| Buss | Modbus RTU över RS485 |
| Fabriksinställning | **9600 baud**, 8N1, adress **1 eller 50** — se nedan |

**Ledare**

| Färg | Funktion |
|---|---|
| Röd | + matning |
| Svart | GND |
| **Blå** | **RS485 A** |
| **Vit** | **RS485 B** |

**Ingen gul ledare.** På pH-givaren är gul A. Koppla efter funktion, inte färg.

**Register som läses** (funktion 0x03, osignerade)

| Register | Värde | Skala |
|---|---|---|
| 0x0100 | Temperatur | ÷ 10 → °C |
| 0x0101 | Grumlighet | ÷ 10 → NTU |

Manualens exempel: `01 03 01 00 00 02 C5 F7` gav `01 03 04 00 C0 03 D9 …`,
alltså 19,2 °C och 98,5 NTU.

Temperaturregistret är **osignerat** och kan inte visa minusgrader. Givaren
är bara specificerad för 0–50 °C, och bojen tas upp före isen.

**Register som finns men inte läses**

| Register | Vad | Varför inte |
|---|---|---|
| 0x0000–0x0003 | Grumlighet, antal decimaler, temperatur, antal decimaler | samma tal; 0x0100–0x0101 har fast skala |
| 0x1000 | Temperaturkalibrering: skriv verklig temperatur × 10 | bara vid kalibrering |
| 0x1001 | Nollpunkt: skriv 0 i destillerat vatten | bara vid kalibrering |
| 0x1003 | Lutning: skriv standardlösningens NTU × 10, t.ex. 10000 i 1000 NTU | bara vid kalibrering |
| **0x2000** | **Adress, 1–255** | används bara vid montering |
| 0x2003 | Baud: 0=4800, 1=9600, 2=19200 | 9600 från fabrik |
| 0x2020 | Fabriksåterställning | **raderar kalibreringen, skriv aldrig** |

**Fabriksadressen är motsägelsefull.** Registertabellen säger att adressen är
50 från fabrik. Varje kommandoexempel i samma manual skickar till adress 01
och kallar 01 "default address". Profilen använder 1, och
`set_address.py --scan` frågar båda. Den första riktiga givaren avgör.

**Placering, enligt manualen.** Minst 5 cm från väggar och minst 10 cm från
botten. Kabeln är bara för signal och får inte bära givaren.

**Underhåll.** Rengör mätfönstret med vatten och mjuk trasa, vid behov lite
diskmedel. Påväxt på fönstret är det största underhållsproblemet i en varm,
näringsrik damm: den sprider ljus själv och får grumligheten att **stiga
långsamt** medan givaren svarar normalt. Det är simulatorns `drift` på
`sensor_2_turb`.

---

## 3. Vad som krockar på bänken

| | Fabriksadress | Fabriksbaud | Ska bli |
|---|---|---|---|
| pH/ORP-givare | 1 | 9600 | adress 1, 9600 baud — **ingen ändring** |
| Grumlighetsgivare | 1 (eller 50) | 9600 | adress 2, 9600 baud |

Bägge går på 9600 från fabrik, så ingen baud behöver ändras. Bägge kan stå på
adress 1, så **grumlighetsgivaren flyttas ensam** innan pH-givaren kopplas in:

1. Koppla in bara grumlighetsgivaren.
2. `python3 set_address.py --scan` — svarar den på 1 eller 50?
3. `python3 set_address.py --sensor turb` (lägg till `--from 50` om den
   svarade på 50).
4. Koppla in pH-givaren. `--scan` ska nu visa 1 och 2.

Görs det i fel ordning rapporterar styrkortet "svarar inte" för två friska
givare.

---

## 4. Matningen: 12 V reglerat, inte batteriet rakt

pH-givaren klarar 4,5–28 V, men grumlighetsgivaren kräver **minst 12 V**. Ett
4S LiFePO4-batteri får sjunka till 10,8 V innan bojen ger upp. Matas railen
direkt från batteriet hamnar grumlighetsgivaren utanför sin specifikation
långt innan batterilarmet går. Sensorrailen ska därför ha en egen
spänningsomvandlare till 12 V (jämför EL-07 och EL-09).

Effekt med båda påslagna: högst 0,12 + 0,30 = 0,42 W, båda övre gränser ur
databladen.

---

## 5. Uppvärmningstid — fortfarande obesvarad

Ingen av givarna anger en uppstartstid.

| Givare | Vad manualen säger | Profilen använder |
|---|---|---|
| pH/ORP | svarstid pH < 10 s, ORP < 30 s | 30 s, `harledd` |
| Grumlighet | ingenting | 10 s, `gissning` |

Svarstid är hur snabbt en igångvarande givare följer en ändring, inte hur
lång tid det tar från strömpåslag till ett pålitligt värde.

Det ska mätas: slå på strömmen, läs varje sekund, och se när värdet slutar
röra sig. Talet styr den största posten i energibudgeten.
