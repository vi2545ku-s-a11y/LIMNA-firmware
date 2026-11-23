# firmware/ — ESP32-koden för LIMNA-bojen

Den här filen är specifikationen som firmwaren skrivs efter. Koden i
`laptop/` är utkastet: `poller.py` gör allt bojen ska göra, men på en laptop
mot simulerade givare. Firmwaren är samma logik flyttad till en ESP32.

## Läget 2026-11-23

**Steg 2 kör på kortet, med simulerade givare.** Kortet mäter varje minut,
sparar i sitt eget minne, skickar över WiFi till Mosquitto på laptopen och
syns i Grafana som bojen `LIMNA-ESP32`. Det startar av sig självt vid
strömpåslag. Steg 1 väntar på RS485-modulen: Modbus-lagret är skrivet och
testat mot manualernas ramar, men inte mot riktiga givare.

| Fil | Gör |
|---|---|
| `main.py` | mätcykeln. MicroPython kör den vid strömpåslag |
| `measure.py` | avkodning, median av tre, flaggor, nyttolast, larmgränser (F-12) |
| `buffer.py` | en fil per dygn i `matningar/`, markeras skickad först när brokern bekräftat (K-03) |
| `net.py` | WiFi, NTP och MQTT. Den enda filen som byts när WiFi blir LoRa eller LTE-M |
| `modbus.py` | Modbus RTU för hand: ramar, CRC, `RTUBus` för UART och DE/RE. **Ej provad mot hårdvara** |
| `fakebus.py` | simulerade givare med samma `read()` som `RTUBus`, inklusive fel värde före uppvärmningen |
| `test_firmware.py` | tester på laptopen, mot bytena i manualerna i `docs/datablad/` |

`delad/sensor_profiles.py` kopieras **oförändrad** till kortet. Den klarar
MicroPython sedan den fick en ersättning för `os.path`.

    make fwtest      testerna, på laptopen
    make esp32       kopiera allt till kortet och starta om det

`config.json` på kortet har WiFi och MQTT och versionshanteras **aldrig**:

```json
{"buoy_id": "LIMNA-ESP32", "buoy_name": "ESP32 testkort",
 "wifi": {"ssid": "...", "password": "..."},
 "mqtt": {"host": "172.20.10.8", "port": 1883,
          "topic": "limna/LIMNA-ESP32/measurements"}}
```

Allt annat har standardvärden i `main.py` (`DEFAULTS`). `"bus": "rtu"` byter
till den riktiga bussen när RS485-modulen sitter på plats.

**Saknas än** (steg 4): djupsömn och RTC-minne, batteri-ADC, MOSFET för
railen, watchdog och kontrollen av fastfrusna värden. Kortet är vaket hela
tiden och drar full ström.

**Tre fällor som redan har bitit:**

- Datamappen får inte heta `buffer`. MicroPython importerar då mappen
  `buffer/` i stället för filen `buffer.py`, och `main.py` stannar vid
  andra starten. Den heter `matningar/`.
- Öppna inte kortets USB-port med ett eget pyserial-skript. Det sätter
  styrlinjerna så att chipet fastnar i återställning eller nedladdningsläge,
  och `main.py` startar inte. Använd `mpremote`.
- **Eduroam går inte.** Det kräver personlig inloggning (WPA2-Enterprise),
  som MicroPython inte stöder, och universitetsnät brukar hindra enheter
  från att nå varandra. Använd mobilens hotspot under utveckling, med
  Maximera kompatibilitet påslaget så att den går på 2,4 GHz. `LU IoT` är
  troligen rätt nät på campus, efter att kortets MAC-adress registrerats.

Läs `CLAUDE.md` i roten först. Den förklarar varför koden ser ut som den gör.

---

## Vad bojen gör

Bojen flyter i en bevattningsdamm under säsongen, ungefär april–oktober. Var
15:e minut:

```
väck  ->  slå på sensorrailen  ->  vänta ut uppvärmningen  ->  läs givarna
      ->  slå av railen  ->  tidsstämpla  ->  skriv till SD-kortet
      ->  skicka om det är dags  ->  djupsömn
```

Ordningen är krav, inte stil. Mätningen skrivs till SD-kortet **innan** något
sändförsök (K-03). En givare som inte svarar får inte stoppa de andra
(EL-03). Ett programfel får aldrig lämna railen påslagen eller bojen vaken
(EL-05).

---

## Språket är MicroPython

Inte C++. Projektägaren är konstruktör, inte utvecklare, och ska kunna läsa
och ändra bojens kod om tre år. `poller.py` går att flytta över nästan mening
för mening, och `delad/sensor_profiles.py` kan köras **oförändrad** på kortet
— det är hela poängen med att registerkartan bara finns på ett ställe.

MicroPython är långsammare och drar något mer ström när den är vaken. För en
boj som är vaken en dryg minut per kvart avgörs energin av uppvärmningstiden,
inte av processorn.

---

## Hårdvaran i prototypen

| Del | Val |
|---|---|
| Styrkort | Waveshare ESP32-S3-DEV-KIT-N32R16V-M (ESP32-S3-WROOM-2-N32R16V: 32 MB **oktal** flash och 16 MB oktal PSRAM, båda 1,8 V). Avläst med `esptool flash-id` 2026-11-23 |
| RS485 | SP3485-modul, 3,3 V, separata DE- och RE-pinnar |
| pH/ORP-givare | Seeed S-pH&ORP-01A, adress 1 |
| Grumlighetsgivare | Seeed S-DTS210-01, adress 2 |
| Matning givare | 12 V **reglerat**, separat från styrkortet, gemensam jord. Grumlighetsgivaren kräver 12–24 V och klarar inte ett batteri som sjunkit under 12 V |

**Pinnar**

| ESP32 | Till | Anmärkning |
|---|---|---|
| GPIO 17 | RS485 DI | UART1 TX |
| GPIO 18 | RS485 RO | UART1 RX |
| GPIO 16 | RS485 DE | hög = sända |
| GPIO 15 | RS485 RE | låg = ta emot (aktivt låg). Styrs ihop med DE |
| 3V3 / GND | RS485 VCC / GND | |
| GPIO 4 | spänningsdelare från batteriet | ADC1. Steg 4 |
| GPIO 5 | MOSFET för sensorrailen | Steg 4 |

Undvik GPIO 0, 3, 45 och 46 (startläge), 19 och 20 (USB), 35–37 (PSRAM) och
43–44 (seriekonsolen). Batterispänningen ska mätas på ADC1, alltså GPIO 1–10,
eftersom ADC2 inte fungerar medan WiFi är på.

Givarna matas med 12 V. Ingen 12 V-ledare får nå en ESP32-pinne eller
RS485-modulens VCC, A eller B.

---

## Givarna och bussen

Allt om register, skalor och adresser står i `delad/sensor_profiles.py`,
profilen `limna-damm-2026`. **Skriv aldrig in ett registernummer i
firmwaren.** Läs det ur profilen.

Det som är bra att veta om just de här givarna:

- **Bussen:** 9600 baud, 8N1. Läs värdena ur profilens `serie`. Båda givarna
  levereras på 9600.
- **Alla värden är 16-bitars heltal med skalfaktor** ur profilen: pH ÷ 100,
  ORP ÷ 10, grumlighet ÷ 10, temperatur ÷ 100 (pH-givaren) eller ÷ 10
  (grumlighetsgivaren). En fråga per värde, funktion 0x03, antal 1.
- **ORP är signerat** och blir negativt i syrefritt vatten. Tvåkomplement:
  `raw - 0x10000` om `raw >= 0x8000`.
- **En givare, flera värden.** pH-givaren på adress 1 ger både `ph` och
  `orp`. Profilen har en rad per givare med en lista `values`.
- **Två termometrar.** `temp` kommer från grumlighetsgivaren
  (`temp_primar` i profilen), och `temp_d` är skillnaden mot pH-givarens.
- **Svarstid:** manualerna anger ingen. Vänta 200 ms på svar som
  utgångspunkt. Tre försök per läsning, sedan felkod och vidare till nästa
  givare.

Registerkartorna är ur tillverkarens manualer (`docs/datablad/`), inte
provade mot hårdvara. Grumlighetsgivarens fabriksadress anges både som 1
och 50. Se `sensor_databla.md`.

---

## Modbus RTU: skriv det själv

Firmwaren behöver bara två funktioner: **0x03** (läs register) och **0x06**
(skriv ett register, för adressbyte). Det är några tiotal rader:

- bygg ramen: adress, funktion, startregister, antal, CRC16 (Modbus,
  polynom 0xA001, startvärde 0xFFFF, låg byte först)
- DE och RE höga, skriv ramen på UART, vänta tills den är skickad
  (`uart.flush()`), DE och RE låga
- läs svaret med timeout, kontrollera adress, funktionskod och CRC
- ett felsvar (funktionskod + 0x80) är ett fel, inte ett värde

Ett eget litet lager går att enhetstesta på laptopen med vanlig Python, utan
kort och utan givare. Ett färdigt bibliotek gör inte det. Känd testvektor:

| Fråga | Ram |
|---|---|
| pH-givaren, adress 1, register 0x0000, 10 register | `01 03 00 00 00 0A C5 CD` |
| Grumlighetsgivaren, adress 1, register 0x0100, 2 register | `01 03 01 00 00 02 C5 F7` |
| Grumlighetsgivaren, adress 1 → 6, register 0x2000 (funktion 0x06) | `01 06 20 00 00 06 02 08` |

Alla tre är manualernas egna exempel. Svaren finns i `sensor_databla.md`.

---

## Mätcykeln, steg för steg

Följ `measure_cycle()` i `laptop/poller.py`. Kortfattat:

1. **Railen på.** GPIO för MOSFET:en hög. I steg 1–3 finns ingen MOSFET, då
   är detta bara en loggrad.
2. **Uppvärmning.** `warmup_s` ur konfigurationen, i dag 30 s.
3. **Läs varje givare tre gånger, ta medianen** (`sample_sensor()`). Flagga
   `brusig` om spridningen är stor och `out_of_range` om värdet ligger utanför
   profilens mätområde. Ett flaggat värde sparas ändå. Ett omöjligt tal är
   bevis och får aldrig kastas eller ersättas.
4. **Läs termometrarna** (`read_thermometers()`). Finns två eller fler blir
   största skillnaden fältet `temp_d`.
5. **Railen av**, i ett `finally`, så att ett undantag aldrig lämnar den på.
6. **Fastfruset värde** (`check_stuck()`): samma värde bitidentiskt i
   `stuck_cycles` mätningar flaggas `stuck`. Räknaren ska ligga i RTC-minnet,
   se nedan.
7. **Batteri** ur ADC (`read_battery()`): `adc.read_uv() * delningsfaktor`.
8. **Nyttolast**, se dataformatet nedan.
9. **Skriv till SD-kortet**, sedan **skicka** om sändfönstret är nått eller
   något värde passerat en gräns i `alarm_limits` (`bradskande()`, F-12).
10. **Energisparläge** (`eco_state()`): under 13,0 V längre intervall, tillbaka
    över 13,2 V.
11. **Djupsömn** resten av intervallet.

Allt som i pollaren skrivs ut med `log()` ska i firmwaren gå till
seriekonsolen, och gärna till en loggfil på SD-kortet.

---

## Dataformatet — får inte ändras

En mätning är en JSON-rad. Fältnamnen används av telegraf, Grafana och
larmreglerna och får inte ändras utan att fråga.

```json
{"boj":"LIMNA-001","namn":"Bevattningsdamm test","t":"2026-07-27T14:00:00+00:00",
 "batt":13.1,"ph":7.83,"orp":239.5,"turb":6.4,
 "temp":18.3,"temp_d":0.25}
```

| Fält | Betyder |
|---|---|
| `boj`, `namn` | ur konfigurationen |
| `t` | tidsstämpel i UTC, ISO 8601, satt **i bojen** när mätningen gjordes |
| `batt` | batterispänning, V |
| `ph` | pH, kompenserat till 25 °C |
| `orp` | redoxpotential, mV |
| `turb` | grumlighet, NTU |
| `temp` | vattentemperatur, °C, från grumlighetsgivaren |
| `temp_d` | största skillnaden mellan de två termometrarna, °C |
| `fel` | bara när något gick fel, t.ex. `{"turb":"no_response"}` |

En givare som inte svarar får **inget** fält, bara en rad i `fel`. Sätt
aldrig in ett gammalt värde eller 0.

MQTT-ämnet är `limna/<boj>/measurements`, QoS 1. Tidsstämpeln kommer från
bojen eftersom en mätning som legat tre dygn i bufferten ska hamna på rätt
ställe i grafen. Klockan ställs med NTP när WiFi finns och hålls av en
DS3231 när det inte gör det (steg 3).

---

## Djupsömn river minnet

`time.sleep` blir `machine.deepsleep()`. Djupsömn är ingen paus utan en
avstängning: vid uppvakning startar programmet om från första raden, och allt
i RAM försvinner.

| Vad | I `laptop/poller.py` | Utan RTC-minne |
|---|---|---|
| `_stuck_history` | RAM | Nollställs varje cykel. Fastfrusetkontrollen löser aldrig ut |
| `last_send` | RAM | Sändfönstret börjar om, radion väcks varje cykel |
| `in_eco` | RAM | Energisparläget fastnar aldrig, F-02 blir verkningslöst |

**Lösningen:** `machine.RTC().memory()`, som överlever djupsömn (några KB).
Spara en liten JSON med `last_send`, `in_eco` och senaste värde plus räknare
per fält. Kontrollera att innehållet går att läsa vid
uppvakning; tomt eller trasigt betyder kallstart, och då börjar allt om.

---

## Bufferten: filer på SD-kortet

Ingen SQLite i MicroPython, och den behövs inte. K-04 kräver 30 dygn:

    152 byte × 96 mätningar/dygn × 30 dygn ≈ 440 KB

En fil per dygn, en JSON-rad per mätning, och en liten fil med hur långt
sändningen har kommit. Skriv mätningen och anropa `flush()` innan något
sändförsök. Markera en rad som skickad först när brokern bekräftat den
(QoS 1). Radera filer äldre än 30 dygn. Ett vanligt microSD-kort räcker
under säsongen.

---

## Uppkopplingen

**I prototypen: WiFi** och `umqtt.simple` mot samma broker som laptopen
(Mosquitto i `docker-compose.yml`). Inga lösenord i koden — de står i en
konfigurationsfil på kortet som inte versionshanteras.

Ute i dammen väljs senare mellan:

- **LoRaWAN.** En gateway på gården tar emot från bojen och från
  markfuktsgivarna. Lägst strömförbrukning och inget SIM. Kräver att
  nyttolasten krymps till några tiotal byte, binärt i stället för JSON.
- **Mobilnät (LTE-M).** PPP mot modemet så att `umqtt` fungerar som över
  WiFi, eller modemets egna MQTT-kommandon.

Skriv därför sändningen som en egen modul med en funktion,
`send(rader) -> antal bekräftade`, så att transporten kan bytas utan att
mätcykeln rörs.

---

## Ordningen — samma steg som prototypplanen

| Steg | Bygg | Klart när |
|---|---|---|
| 1 | Modbus RTU mot pH/ORP-givaren och grumlighetsgivaren, utskrift i seriekonsolen | Båda givarna ger rimliga värden, pH visar ca 7 i kranvatten och 4,00 i buffertlösning, och grumligheten stiger när vattnet rörs upp |
| 2 | WiFi, NTP och MQTT, samma nyttolast som pollaren | Värdena syns i Grafana medan man tittar, och kortet återansluter efter WiFi-avbrott |
| 3 | DS3231 och SD-bufferten | En timme utan WiFi ger inga luckor, med rätt tidsstämplar. Samma efter strömavbrott |
| 4 | Djupsömn, RTC-minne, MOSFET för railen, batterimätning, watchdog | Förbrukningen är uppmätt under ett dygn: sömn, mätcykel och uppvärmning |
| 5 | Radiolänk eller mobilnät i stället för WiFi | Data kommer fram från dammen och bufferten töms efter avbrott |
| 6 | Sol, batteri och laddregulator (ingen kod, bara mätning) | En vecka utan eluttag utan att batteriet sjunker |
| 7 | Fältversion | Två veckor i dammen utan fel |

**Mät sömnströmmen tidigt.** En bar ESP32-modul drar runt tio mikroampere i
djupsömn. Waveshare-kortet har USB-kretsar och lysdiod och drar troligen
milliampere — tusen gånger mer. Det duger på bänken men troligen inte i
bojen, och det syns ingenstans i koden.

`machine.WDT` ger EL-05 nästan gratis: hänger programmet startar kortet om.

---

## Testa utan hårdvara

Givarna är inte levererade. Bygg så att det mesta går att testa ändå.

1. **På laptopen, med vanlig Python.** CRC, rambygge, avkodning (signerat och
   osignerat), median, flaggor, nyttolast och bufferten är ren Python utan
   `machine`. Lägg dem i moduler som inte importerar `machine` och testa dem
   med `pytest`. Jämför mot `laptop/poller.py` — samma indata ska ge samma
   nyttolast.
2. **På kortet, utan givare.** En `FakeBus` med samma funktioner som den
   riktiga Modbus-klassen, som svarar med värden ur samma modell som
   `laptop/fake_sensors.py`: pH som svänger över dygnet, grumlighet som
   stiger när det bevattnas eller regnar, och fel värde om man läser före
   uppvärmningen. Välj
   buss i konfigurationen.
3. **På kortet, med riktig buss men simulerade givare.** USB–RS485-adapter på
   laptopen och `fake_sensors.py` som Modbus RTU-slav. I dag kör simulatorn
   bara TCP; ett `--rtu`-läge med pymodbus serieserver är en liten ändring.

---

## Konfiguration på kortet

En `config.json` på kortets filsystem, med samma nycklar som
`laptop/config.json` där de gäller: `buoy_id`, `buoy_name`,
`sensor_profile`, `measure_interval_s`, `send_interval_s`, `warmup_s`,
`retries`, `alarm_limits`, `eco_mode`, `stuck_cycles`, `sampling`, `mqtt`. Lägg till `wifi` (SSID och lösenord) och `pins` om
pinnarna ska gå att ändra utan ny kod.

---

## Kända fallgropar

- `delad/sensor_profiles.py` använder `os.path`, som inte finns i
  MicroPython. Lös det **i den filen** med en enkel ersättning när `os.path`
  saknas — gör aldrig en egen kopia av profilen.
- `dict(profile, sensors=...)` i `load_profile()`: kontrollera att den
  fungerar i MicroPython-versionen ni flashar.
- Använd MicroPythons **SPIRAM_OCT**-bygge för ESP32_GENERIC_S3. Provat
  2026-11-23: `ESP32_GENERIC_S3-SPIRAM_OCT-20260824-v1.29.0.bin` startar på
  kortet trots den oktala flashen, ser alla 32 MB flash och ger ca 16,6 MB
  fritt RAM. Standardbygget utan SPIRAM_OCT använder inte PSRAM:et.
  Flashas med `esptool --chip esp32s3 erase-flash`, sedan
  `esptool --chip esp32s3 --baud 460800 write-flash 0 <fil>.bin`.
- Kortet syns som två portar på Mac. `/dev/cu.usbmodem1101` (chipets egen
  USB) är den som används för flashning och för MicroPythons prompt.
- Båda givarna kan levereras på **adress 1**. Flytta grumlighetsgivaren till
  2 ensam innan pH-givaren kopplas in (`laptop/set_address.py --sensor turb`
  över USB–RS485). Svarar den inte på 1, prova 50.
- pH-givaren tar en ny adress först efter strömcykel, om den någon gång
  behöver flyttas.
- Förvara pH-elektroden i 3 M KCl eller med skyddet på när bojen är upptagen
  över vintern. Den förstörs av att torka ut.

---

## Utanför det första bygget

OTA-uppdatering, fjärrkonfiguration, GPS, accelerometer, LoRa-nyttolast,
TLS (K-02) och markfuktsgivarna. Lämna tydliga ställen där de kopplas in.
