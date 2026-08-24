# LIMNA — sensorernas datablad

Sammanställt 28 juli 2026 från Seeed Studios officiella databladsfiler.
Alla siffror nedan är **hämtade ur databladen**, inte uppskattade.

| Sensor | Modell | SKU | Datablad |
|---|---|---|---|
| Löst syre | S-RJY-01 | 314990633 | `files.seeedstudio.com/wiki/Dissolved-Oxygen-Sensor/Datasheet-Dissolved_Oxygen_Sensor.pdf` |
| Turbiditet | S-DTS210-01 | 101990793 | `files.seeedstudio.com/products/SenseCAP/101990793/Industrial Turbidity Sensor User Manual.pdf` |
| Konduktivitet | S-EC-01 | 314990634 | `files.seeedstudio.com/products/314990634/res/Liquid EC and TDS Sensor Datasheet v1.1.pdf` |
| Nivå | S-YW-01B | 101990860 | `files.seeedstudio.com/products/101990860/Datasheet-RS485 Liquid Level Sensor.pdf` |
| **pH** | **S-pH-01A** | **ej noterad** | **saknas i sammanställningen — se nedan** |

**pH-givarens datablad saknas i tabellen ovan.** Siffrorna för pH i kapitel
1, 3, 4, 5, 6, 8 och 10 nedan är hämtade ur databladet, men länken skrevs
aldrig ned när sammanställningen gjordes 28 juli. Den som nästa gång ska
kontrollera en pH-uppgift får leta upp den igen. Komplettera raden när ni
har den — det är den enda av de fem vars källa inte går att klicka sig till.

---

## 1. Registerkartan — ersätter den påhittade

Detta är det som ska in i `SENSORS` i `poller.py` och `REGISTERS` i
`fake_sensors.py`. **Ingen av mina gissningar stämde.**

| Sensor | Register | Storhet | Skala | Typ |
|---|---|---|---|---|
| **Syre** | `0x0101` | mg/L | ÷ 100 | osignerad |
| | `0x0100` | °C | ÷ 10 | osignerad |
| | `0x0102` | mättnad % | ÷ 10 | osignerad |
| **Turbiditet** | `0x0101` | NTU | ÷ 10 | osignerad |
| | `0x0100` | °C | ÷ 10 | osignerad |
| **Konduktivitet** | `0x0002` | µS/cm | **÷ 1** | osignerad |
| | `0x0000` | °C | ÷ 100 | **SIGNERAD** |
| | `0x0003` | salthalt mg/L | ÷ 1 | osignerad |
| | `0x0004` | TDS mg/L | ÷ 1 | osignerad |
| **pH** | `0x0001` | pH | ÷ 100 | osignerad, 0–1400 |
| | `0x0000` | °C | ÷ 100 | **SIGNERAD** |
| **Nivå** | `0x0004` | mätvärde | **se nedan** | **SIGNERAD** |
| | `0x0002` | enhet | — | 16 = meter |
| | `0x0003` | decimaler | — | 0–3 |

**Tre fällor:**

Konduktivitetens temperatur är **signerad**. Läser du den som osignerad blir
−2,51 °C till 65 269. Nivåsensorn är också signerad.

Nivåsensorns skala är **konfigurerbar**, inte fast. Register `0x0003` säger
hur många decimaler: 0 = heltal, 1 = ×10, 2 = ×100, 3 = ×1000. Läs det en
gång vid uppstart i stället för att hårdkoda.

Konduktiviteten har **skala 1**. Registervärdet 1568 betyder 1568 µS/cm rakt av.

**Bonus:** temperatur finns i **fyra av fem** sensorer — syre, turbiditet,
konduktivitet och pH. Fyra oberoende mätningar av samma storhet är gratis
redundans, och skillnaden mellan dem avslöjar en sensor som driver.

Koden läser i dag bara konduktivitetssensorns temperatur. Vad som ska göras
med de övriga tre är ett öppet designval.

---

## 2. Modbus-adresser — två av fem krockar

Rubriken sa till 2026-08-10 att de **inte** krockar, vilket motsägs av
kapitlets eget innehåll: konduktivitet och pH har båda adress 1. Tre av fem
kolliderar inte — 55, 50 och 26. Två gör det.

Fabriksadresserna:

| Sensor | Fabriksadress | Ändras via | Intervall |
|---|---|---|---|
| Syre | **55** | `0x2000` | 1–127 |
| Turbiditet | **50** | `0x2000` | 1–255 |
| Konduktivitet | **1** (eller 30) | `0x0200` | 0–255 |
| pH | **1** | `0x0200` | 0–255 |
| Nivå | **26** (0x1A) | `0x0000` | 1–255 |

**Registren för adressändring är OVERIFIERADE mot hårdvara.** De tre olika
registren ovan — `0x2000`, `0x0200` och `0x0000` — står i databladen, men
ingen har provat att skriva till dem. README och koden påstod till
2026-08-10 att alla fem ändras via `0x0200`, vilket motsägs av den här
tabellen. Tabellen gäller, men kontrollera mot databladets sida innan
monteringsdagen, inte under den. Registren finns nu i `sensor_profiles.py`,
per givare, med samma reservation.

**KONDUKTIVITET OCH pH HAR BÅDA ADRESS 1.** De kolliderar alltså. Kopplas båda
in innan adressen ändrats svarar de samtidigt på samma trådar och svaret blir
oläsligt — inte två meddelanden utan ett skräpmeddelande. Styrkortet rapporterar
"svarar inte", vilket är missvisande.

De måste adresseras **en i taget**. Detta är precis varför steg 2 i
monteringsplanen finns.

Övriga tre — 55, 50 och 26 — kolliderar inte med något.

Samtliga: 9600 baud, 8 databitar, ingen paritet, 1 stoppbit.

Konduktivitetssensorn har en **återställningsknapp** — håll SW1-SET i tre
sekunder så återgår kommunikationsinställningarna till fabriksläge. Bra att
veta om ni glömmer bort vad ni satt.

---

## 3. Strömförbrukning — det viktigaste fyndet

| Sensor | Databladets uppgift | Vid 12 V |
|---|---|---|
| Syre | 0,3 W @ 12 V | **0,30 W** |
| Turbiditet | 0,3 W @ 12 V | **0,30 W** |
| Konduktivitet | 40 mA @ 24 V vila, 80 mA @ 24 V max | **0,48–1,92 W** (se nedan) |
| **pH** | **35 mA @ 24 V** | **0,84 W** |
| Nivå | **ej angiven** | okänd |

**Osäkerheten kring konduktiviteten:** databladet anger ström vid 24 V. Om
matningen är en switchad omvandlare drar den ungefär samma **effekt** vid 12 V,
alltså 0,96 W vid vila. Är den linjär drar den samma **ström**, alltså 0,48 W.
Det går inte att avgöra ur databladet. Mät.

**Vad det betyder för energibudgeten:**

| | Fyra sensorer, antaget | Fem sensorer, enligt datablad |
|---|---|---|
| Sensorrail totalt | 2,40 W | **2,85 W** |
| Sensorrailens andel | 5,76 Wh/dygn | 6,84 Wh/dygn |
| Total last | 9,16 Wh/dygn | **12,84 Wh/dygn** |
| Autonomi | 28,5 dygn | **20,3 dygn** |
| Minsta panel i november | 27,4 Wp | 38,3 Wp |

**Autonomikravet på 21 dygn uppfylls därmed inte med odelad sensorrail.**

De två sensorer som drar mest — konduktivitet och pH — är också de som blir
klara snabbast. Det är hela argumentet för delad rail, se kapitel 4.

---

## 4. Uppvärmningstid — fortfarande obesvarad

Detta var huvudsyftet med att hämta databladen, och svaret är en besvikelse.

| Sensor | Uppvärmningstid enligt datablad |
|---|---|
| Syre | **anges inte** |
| Turbiditet | **anges inte** |
| Konduktivitet | **< 2 sekunder** (uttryckligt) |
| **pH** | **< 2 sekunder** (uttryckligt) |
| Nivå | svarstid 5 ms (men det är responstid, inte uppstartstid) |

Tillverkaren specificerar alltså inte uppstartstid för de två optiska
sensorerna. Sannolikt för att de flesta kunder låter sensorn vara påslagen
kontinuerligt.

**Slutsatsen står kvar: 90 sekunder är fortfarande en gissning som måste
mätas** — men bara för de två optiska sensorerna.

Att BÅDE konduktivitet och pH är klara på under två sekunder gör argumentet
för delad rail avgörande, inte bara fördelaktigt. Dessa två drar tillsammans
1,8 av railens 2,85 W och behöver 2 av dess 90 sekunder.

En ledtråd från Seeeds övriga sensorer: deras CO2-sensor returnerar **0** före
uppvärmningstiden, inte ett felaktigt värde. Om samma gäller här blir
mjukvarulogiken enklare — läs tills värdet inte längre är noll.

---

## 5. Temperaturgränser — en konflikt med kravspecen

| Sensor | Drift | Förvaring |
|---|---|---|
| Syre | **0 till 50 °C** | **−5 till 65 °C** |
| Turbiditet | **0 till 50 °C** | **−5 till 65 °C** |
| Konduktivitet | −40 till 85 °C | — |
| pH | −40 till 85 °C | — |
| Nivå | medium −40 till 80 °C | −40 till 85 °C |

**Krav E-09 i kravspecen säger förvaring ned till −20 °C. Syre- och
turbiditetssensorn klarar bara −5 °C.**

Det betyder att vinterförvaringen inte får ske i ett ouppvärmt förråd. Kapitel
7.3 i monteringsplanen säger "förvara frostfritt om det går" som en
rekommendation — det ska skärpas till ett krav för dessa två sensorer.

Att de inte heller får **drivas** under 0 °C är ett indirekt stöd för
säsongsbeslutet. I isfritt vatten är vattentemperaturen alltid över noll, så
sensorn i vattnet klarar sig. Men det bekräftar att bojen inte hör hemma i is.

---

## 6. Mekaniskt — en detalj som saknas i alla våra ritningar

**Både konduktivitets- och pH-sensorn är två delar, inte en.** Elektroden går i
vattnet, men den kopplas till en separat sändarbox som **inte får bli blöt**:

| | |
|---|---|
| EC-elektrod | IP68, 1/2" NPT-gänga, 10 m kabel |
| EC-sändarbox | **IP65**, 128 × 70 × 42 mm, 2 m kabel |
| pH-elektrod | **IP68**, 160 × 30 mm, 3/4" NPT-gänga, 5 m kabel |
| pH-sändarbox | **IP65**, 140 × 65 × 50 mm, 2 m kabel |

Databladet är uttryckligt: *"Do not put the Transmitter into the liquid."*

**Två boxar** måste alltså sitta i elektronikskåpet, tillsammans cirka
18 000 mm² av bottenytan, plus två genomföringar för elektrodkablarna.

Det är hela skälet till att skåpet i komponentlistan inte räcker. Se fliken
Utrymme i energibudgeten.

**Övriga mekaniska mått:**

| Sensor | Montering | Kabel | Vikt |
|---|---|---|---|
| Syre | 3/4" NPT, nedsänkt | 10 m | — |
| Turbiditet | 3/4" NPT, nedsänkt | 10 m | — |
| Konduktivitet | 1/2" NPT | 10 + 2 m | — |
| pH | 3/4" NPT | 5 + 2 m | 400 g |
| Nivå | — | **5,3 m** | **670 g** |

Nivåsensorns kabel på 5,3 meter är den kortaste och begränsar hur djupt den
kan hänga. Sensorkorgen ligger på 0,5–0,8 m, så det räcker gott — men om ni
senare vill mäta på flera djup är det kabeln som sätter gränsen.

Alla tre vattensensorer använder M16 5-polig vattentät kontakt.

---

## 7. Installation

Turbiditetssensorn har ett uttryckligt krav som gäller sensorkorgens
utformning:

> Minst **5 cm från sidoväggar** och minst **10 cm från botten**.

Det gäller rimligen syresensorn också, eftersom båda är optiska och mäter
spritt ljus. Sensorkorgen får alltså inte vara en trång bur.

Syresensorns datablad varnar dessutom för att den fluorescerande membranhuvudet
inte får komma i kontakt med bottensediment, och att gummiskyddet ska tas bort
före användning.

---

## 8. Kalibrering och service

| Sensor | Kalibrering | Lösningar som behövs |
|---|---|---|
| Syre | Tvåpunkt | Syrefritt vatten + luftmättat vatten |
| Turbiditet | Tvåpunkt | Destillerat vatten (0 NTU) + 1000 NTU standard |
| Konduktivitet | Tvåpunkt | **1413 µS/cm + 12880 µS/cm** |
| **pH** | **Trepunkt** | **Buffert pH 4,01 + 7,00 + 10,01** |
| Nivå | Nollpunkt | — |

Konduktiviteten kan kalibreras med knappar på sändarboxen i stället för
Modbus-kommandon, vilket är enklare i fält.

**Syresensorns underhållsschema enligt databladet:**

| Åtgärd | Intervall |
|---|---|
| Rengör givaren | var 30:e dag |
| Kontrollera membranhuvudet | var 30:e dag |
| Byt fluorescerande membranhuvud | vart 1–2 år |
| Kalibrera | var 3–6:e månad |

Trettio dagar är **kortare** än de 6–8 veckor vi antagit i affärsmodellen.
Det påverkar serviceåtagandet och därmed prissättningen. Ta upp det med Lars.

---

## 9. Vad detta ändrar i projektet

**Måste rättas i koden:**
1. Registerkartan i `poller.py` och `fake_sensors.py` — samtliga fyra fel
2. Signerad läsning för konduktivitetens temperatur och nivåvärdet
3. Nivåsensorns skala ska läsas ur register `0x0003`, inte hårdkodas

**Måste rättas i dokumenten:**
4. Energibudgeten: sensorrailens effekt 2,40 → 1,4–2,0 W
5. Kravspec E-09: förvaring −20 °C är omöjligt för två av sensorerna
6. Monteringsplan kapitel 7.3: frostfri förvaring blir ett krav, inte en rekommendation
7. Monteringsplan steg 2: adresserna krockar inte, varningen kan mjukas upp
8. Elektronikskåpet måste rymma EC-sändaren, 128 × 70 × 42 mm

**Måste tas upp med Lars:**
9. Rengöring var 30:e dag enligt tillverkaren, mot 6–8 veckor i vår affärsmodell

**Fortfarande okänt:**
10. Uppvärmningstid för syre och turbiditet — måste mätas
11. Nivåsensorns strömförbrukning — den enda sensor utan angiven siffra
12. Konduktivitetens och pH:s effekt vid 12 V — databladen anger bara 24 V

---

## 10. Två varningar vid inköp och bygge

**Välj rätt pH-variant.** Det finns två: en med lösa hook-up-ledningar och en
med vattentät aviation-kontakt. Välj **aviation-varianten**. Lösa ledningar i
en boj är en läckagepunkt, och övriga sensorer använder redan M16-kontakter.

**pH-sensorns kabelfärger skiljer sig från de andra.** På syresensorn är blå
RS485-A. På pH-sensorn är **blå en 4–20 mA strömutgång** och A är **gul**.
Kopplar du pH som du kopplade syresensorn matar du in en strömutgång på
databussen.